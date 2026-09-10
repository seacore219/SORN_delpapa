import argparse
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
COMMON_DIR = REPO_ROOT / "common"
TEST_SINGLE = COMMON_DIR / "test_single.py"
GENERATED_PKG = "sweep_params"
GENERATED_DIR = REPO_ROOT / GENERATED_PKG

# test_single.py hardcodes utils.initialise_backup(mount="../", dest="../backup"),
# so every run creates <repo>/backup/test_single/<YYYY-MM-DD HH-MM-SS>/.
DEFAULT_BACKUP_ROOT = REPO_ROOT / "backup"
BACKUP_SCRIPT_DIR = "test_single"

# Some quantities live in more than one place in the parameter files.  Sweeping
# the short name keeps them consistent (this is how the param_*.py files ship
# them, e.g. c.h_ip == c.W_ei.h_ip == 0.1).  Disable with --no-linked-params.
LINKED_PARAMS = {
    "h_ip": ("h_ip", "W_ei.h_ip"),
}

PY2_CANDIDATES = ("python2", "python2.7", "python2.6", "python")

# common/sorn.py writes '\rSimulation: NN%' while c.display is True, once per
# percent of each simulation phase.  Reading it off the child's stdout is what
# drives the progress display -- no simulation code is involved.
PROGRESS_RE = re.compile(r"Simulation:\s*(\d+)\s*%")
TOTAL_STEPS_PREFIX = "[run_sweep] total_steps="
TOTAL_STEPS_RE = re.compile(re.escape(TOTAL_STEPS_PREFIX) + r"(\d+)")


# --------------------------------------------------------------------------
# sweep specification parsing
# --------------------------------------------------------------------------
def format_decimal(value):
    """Decimal -> plain string without exponent or trailing zeros."""
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_scalar(text):
    """Return a Decimal for numeric tokens, else the raw (python literal) text."""
    token = text.strip()
    if not token:
        raise ValueError("empty value")
    try:
        return Decimal(token)
    except InvalidOperation:
        return token


def parse_values(spec):
    """Parse ``start:stop:step``, ``v1,v2,...`` or a single value."""
    spec = spec.strip()
    if ":" in spec:
        parts = [p.strip() for p in spec.split(":")]
        if len(parts) != 3:
            raise ValueError(
                "range must be start:stop:step (got %r)" % spec)
        try:
            start, stop, step = (Decimal(p) for p in parts)
        except InvalidOperation:
            raise ValueError("range bounds must be numeric (got %r)" % spec)
        if step == 0:
            raise ValueError("step must not be zero (got %r)" % spec)
        if (stop - start) * step < 0:
            raise ValueError(
                "step points away from stop (got %r)" % spec)
        values = []
        index = 0
        while True:
            # Exact decimal arithmetic: no 0.06000000000000001 in the output.
            value = start + step * index
            if step > 0 and value > stop:
                break
            if step < 0 and value < stop:
                break
            values.append(value)
            index += 1
        return values
    if "," in spec:
        return [parse_scalar(p) for p in spec.split(",") if p.strip()]
    return [parse_scalar(spec)]


def parse_assignment(text, what):
    if "=" not in text:
        raise argparse.ArgumentTypeError(
            "%s must be given as name=value (got %r)" % (what, text))
    name, _, value = text.partition("=")
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("%s has an empty name: %r" % (what, text))
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$", name):
        raise argparse.ArgumentTypeError(
            "%s name must be a (dotted) parameter name (got %r)" % (what, name))
    return name, value


def sweep_type(text):
    name, value = parse_assignment(text, "--sweep")
    try:
        values = parse_values(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--sweep %s: %s" % (name, error))
    if not values:
        raise argparse.ArgumentTypeError("--sweep %s: no values" % name)
    return name, values


def set_type(text):
    name, value = parse_assignment(text, "--set")
    return name, parse_scalar(value)


# --------------------------------------------------------------------------
# code generation
# --------------------------------------------------------------------------
def value_literal(value):
    if isinstance(value, Decimal):
        return format_decimal(value)
    return value  # raw text, inserted verbatim (True, 'abc', np.inf, ...)


def value_label(value):
    if isinstance(value, Decimal):
        return format_decimal(value)
    return str(value)


def sanitize(text):
    """Make a string safe for use inside a python module name."""
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_")


def assignment_targets(name, link_params):
    if link_params and name in LINKED_PARAMS:
        return list(LINKED_PARAMS[name])
    return [name]


def render_param_module(base_module, assignments, file_suffix, vary_param,
                        extra_code=()):
    """Source of a parameter module overriding `assignments` on top of the base."""
    lines = [
        "# Auto-generated by run_sweep.py -- edits will be lost.",
        "from __future__ import division",
        "import utils",
        "utils.backup(__file__)",
        "",
        "from %s import *" % base_module,
        "from %s import c" % base_module,
        "",
    ]
    for name, value in assignments:
        lines.append("c.%s = %s" % (name, value_literal(value)))
    lines.append("")
    lines.append("c.stats.file_suffix = %r" % file_suffix)
    if vary_param is not None:
        name, value = vary_param
        lines.append("c.cluster.vary_param = %r" % name)
        lines.append("c.cluster.current_param = %s" % value_literal(value))
    if extra_code:
        lines.append("")
        lines.append("import numpy as np  # convenience for --exec snippets")
        lines.append("# --exec")
        lines.extend(extra_code)
    # Reported last, so it reflects every override above: run_sweep.py reads it
    # from the simulation's output to estimate how long the sweep will take.
    lines.append("")
    lines.append("try:")
    lines.append("    print('%s%%d' %% c.N_steps)" % TOTAL_STEPS_PREFIX)
    lines.append("except Exception:")
    lines.append("    pass")
    lines.append("")
    return "\n".join(lines)


def dependent_lines(param_module, name):
    """Lines of the base param file that compute something from `c.<name>`.

    Overrides are applied after the base file has run, so anything the file
    derived from a swept parameter keeps its original value.  The formulas
    differ between param files (param_Zheng2013 has no steps_perturbation at
    all), so the file itself is quoted rather than a guessed formula.
    """
    path = REPO_ROOT.joinpath(*param_module.split(".")).with_suffix(".py")
    try:
        source = path.read_text().splitlines()
    except OSError:
        return []
    uses = re.compile(r"\bc\.%s\b" % re.escape(name))
    assigns = re.compile(r"^\s*c\.%s\s*=" % re.escape(name))
    return [line.strip() for line in source
            if uses.search(line) and not assigns.match(line)
            and not line.strip().startswith("#")]


def ensure_generated_package():
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    init_file = GENERATED_DIR / "__init__.py"
    if not init_file.exists():
        init_file.write_text(
            '"""Parameter modules generated by run_sweep.py."""\n')


def remove_generated_module(module_path):
    """Remove a generated module and any byte-compiled leftovers."""
    stale = [module_path, module_path.with_suffix(".pyc")]
    stale.extend((module_path.parent / "__pycache__").glob(
        module_path.stem + ".*.pyc"))
    for path in stale:
        try:
            path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# run plan
# --------------------------------------------------------------------------
class Run(object):
    def __init__(self, index, repeat, values, label, module_name, module_path):
        self.index = index
        self.repeat = repeat            # 1-based repetition of this parameter set
        self.values = values            # list of (name, value)
        self.label = label              # e.g. 'h_ip_0.02'
        self.module_name = module_name  # e.g. 'sweep_params.param_...'
        self.module_path = module_path
        self.status = "pending"
        self.returncode = None
        self.total_steps = None     # reported by the generated param module
        self.phase = 0              # simulation phases seen so far
        self.percent = None         # progress within the current phase
        self.phase_started = None
        self.backup_dir = None
        self.result_dir = None
        self.started = None
        self.finished = None
        self.log_path = None

    def as_dict(self):
        return {
            "index": self.index,
            "repeat": self.repeat,
            "label": self.label,
            "values": {name: value_label(value) for name, value in self.values},
            "param_module": self.module_name,
            "status": self.status,
            "returncode": self.returncode,
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
            "result_dir": str(self.result_dir) if self.result_dir else None,
            "started": self.started,
            "finished": self.finished,
            "duration_s": (round(self.finished - self.started, 1)
                           if self.started and self.finished else None),
            "total_steps": self.total_steps,
            "log": str(self.log_path) if self.log_path else None,
        }


def build_runs(args, sweep_id):
    base_name = args.param.rsplit(".", 1)[-1]
    if base_name.startswith("param_"):
        base_name = base_name[len("param_"):]

    names = [name for name, _ in args.sweep]
    value_lists = [values for _, values in args.sweep]
    combinations = list(itertools.product(*value_lists)) if names else [()]

    runs = []
    index = 0
    for combination in combinations:
        swept = list(zip(names, combination))
        if swept:
            label = "__".join("%s_%s" % (name, value_label(value))
                              for name, value in swept)
        else:
            label = "baseline"
        for repeat in range(1, args.repeats + 1):
            index += 1
            module_stem = "param_%s_%s_r%02d_%s" % (
                sanitize(base_name), sanitize(label), repeat, sweep_id)
            runs.append(Run(
                index=index,
                repeat=repeat,
                values=swept,
                label=label,
                module_name="%s.%s" % (GENERATED_PKG, module_stem),
                module_path=GENERATED_DIR / (module_stem + ".py"),
            ))
    return runs


def write_param_module(run, args):
    assignments = []
    for name, value in run.values:
        for target in assignment_targets(name, args.link_params):
            assignments.append((target, value))
    for name, value in args.set:
        for target in assignment_targets(name, args.link_params):
            assignments.append((target, value))

    vary_param = None
    numeric_swept = [(name, value) for name, value in run.values
                     if isinstance(value, Decimal)]
    if len(run.values) == 1 and len(numeric_swept) == 1:
        vary_param = numeric_swept[0]

    file_suffix = "%s_r%02d" % (run.label, run.repeat)
    source = render_param_module(args.param, assignments, file_suffix, vary_param,
                                 extra_code=args.exec_code)
    run.module_path.write_text(source)
    return source


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def interpreter_major(executable):
    try:
        out = subprocess.run(
            [executable, "-c", "import sys; print(sys.version_info[0])"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30).stdout.decode().strip()
        return int(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def detect_interpreter(explicit):
    if explicit:
        return explicit
    for candidate in PY2_CANDIDATES:
        path = shutil.which(candidate)
        if not path:
            continue
        if interpreter_major(path) == 2:
            return path
    return None


def backup_run_root(args):
    return Path(args.backup_root) / BACKUP_SCRIPT_DIR


def list_backup_dirs(args):
    root = backup_run_root(args)
    if not root.is_dir():
        return set()
    return {entry.name for entry in root.iterdir() if entry.is_dir()}


class SweepRunner(object):
    """Runs the plan, sequentially or with a small thread pool."""

    def __init__(self, args, runs, sweep_dir, interpreter):
        self.args = args
        self.runs = runs
        self.sweep_dir = sweep_dir
        self.interpreter = interpreter
        self.started = time.time()
        self.print_lock = threading.Lock()
        self.tty = sys.stdout.isatty()
        self.status_interval = args.progress_interval
        if self.status_interval is None:
            self.status_interval = 1.0 if self.tty else 60.0
        self.show_status = self.status_interval > 0
        self.status_width = 0
        self.last_status = 0.0
        self.ticker = None
        # test_single.py names its backup directory after the current second,
        # so two simulations starting within the same second would share (and
        # clobber) it.  Only one run is allowed in its start-up phase at a
        # time: the next one is launched once the previous directory exists.
        self.launch_lock = threading.Lock()
        self.claimed_dirs = set(list_backup_dirs(args))
        self.stop = threading.Event()
        self.processes = {}

    def log(self, message):
        with self.print_lock:
            self._erase_status()
            print(message, flush=True)
            self._draw_status(force=True)

    # -- progress display ---------------------------------------------------
    def _erase_status(self):
        """Blank the in-place status line (caller holds the print lock)."""
        if self.tty and self.status_width:
            sys.stdout.write("\r" + " " * self.status_width + "\r")
            sys.stdout.flush()
            self.status_width = 0

    def _draw_status(self, force=False):
        """Render the status line (caller holds the print lock)."""
        if not self.show_status:
            return
        now = time.time()
        if not force and now - self.last_status < self.status_interval:
            return
        if not any(run.status == "running" for run in self.runs):
            return
        text = self.status_text(now)
        self.last_status = now
        if self.tty:
            width = shutil.get_terminal_size((100, 24)).columns - 1
            text = text[:width]
            sys.stdout.write("\r" + text)
            sys.stdout.flush()
            self.status_width = len(text)
        else:
            print(text, flush=True)

    def tick(self):
        with self.print_lock:
            self._draw_status()

    def start_ticker(self):
        """Keep the elapsed time and ETA moving while the simulations are quiet."""
        if not self.show_status:
            return

        def loop():
            while not self.ticker_stop.wait(min(self.status_interval, 1.0)):
                self.tick()

        self.ticker_stop = threading.Event()
        self.ticker = threading.Thread(target=loop, daemon=True)
        self.ticker.start()

    def stop_ticker(self):
        if self.ticker is not None:
            self.ticker_stop.set()
            self.ticker.join(timeout=2)
            self.ticker = None
        with self.print_lock:
            self._erase_status()

    def status_text(self, now):
        done = [r for r in self.runs if r.status == "done"]
        active = [r for r in self.runs if r.status == "running"]
        parts = ["[%d/%d done%s]" % (
            len(done), len(self.runs),
            " · %d running" % len(active) if len(active) > 1 else "")]
        for run in active[:3]:
            fragment = "%s r%d" % (run.label, run.repeat)
            if run.percent is not None:
                fragment += " phase %d %d%%" % (run.phase, run.percent)
            fragment += " " + format_duration(now - run.started)
            estimate = self.run_eta(run, now)
            if estimate is not None:
                seconds, kind = estimate
                fragment += " (%s %s)" % (kind, format_duration(seconds))
            parts.append(fragment)
        if len(active) > 3:
            parts.append("+%d more" % (len(active) - 3))
        parts.append("sweep " + format_duration(now - self.started))
        eta = self.sweep_eta(now)
        if eta is None:
            parts.append("ETA -- (after the first run)")
        else:
            parts.append("ETA %s, done ~%s" % (
                format_duration(eta),
                time.strftime("%H:%M", time.localtime(now + eta))))
        return " · ".join(parts)

    # -- estimates ----------------------------------------------------------
    def predicted_duration(self, run):
        """Expected wall time of a run, from the runs that already finished."""
        done = [r for r in self.runs
                if r.status == "done" and r.started and r.finished]
        if not done:
            return None
        durations = [r.finished - r.started for r in done]
        steps = [r.total_steps for r in done]
        if all(steps):
            # Scale by simulated steps, so sweeping N_steps still predicts well.
            per_step = sum(durations) / float(sum(steps))
            typical = sorted(steps)[len(steps) // 2]
            return per_step * (run.total_steps or typical)
        return sum(durations) / float(len(durations))

    def run_eta(self, run, now):
        """(seconds, label) left for a running simulation, or None."""
        predicted = self.predicted_duration(run)
        if predicted is not None:
            return max(predicted - (now - run.started), 0.0), "ETA"
        # Nothing has finished yet: the current phase is all we can extrapolate.
        if run.percent and run.phase_started:
            elapsed = now - run.phase_started
            if elapsed > 0:
                return elapsed * (100.0 - run.percent) / run.percent, "phase ETA"
        return None

    def sweep_eta(self, now):
        """Seconds left for the whole sweep, or None while nothing has finished."""
        remaining = 0.0
        running = [0.0]
        for run in self.runs:
            if run.status == "pending":
                predicted = self.predicted_duration(run)
                if predicted is None:
                    return None
                remaining += predicted
            elif run.status == "running":
                estimate = self.run_eta(run, now)
                if estimate is None or estimate[1] != "ETA":
                    return None
                remaining += estimate[0]
                running.append(estimate[0])
        # The work is shared between --jobs workers, but the sweep cannot end
        # before the longest running simulation does.
        return max(remaining / max(self.args.jobs, 1), max(running))

    def environment(self):
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        if self.args.mpl_backend:
            env["MPLBACKEND"] = self.args.mpl_backend
        return env

    def claim_new_backup_dir(self, process, timeout):
        """Wait for this run's backup directory to appear and claim its name.

        The directory name has a one second resolution, and utils.backup only
        creates it when it does not exist yet, so two simulations starting
        within the same second would silently write into the same directory.
        Returning only once the current second has elapsed guarantees that the
        next simulation (launched under the same lock) gets its own name.
        """
        deadline = time.time() + timeout
        try:
            while time.time() < deadline and not self.stop.is_set():
                new = list_backup_dirs(self.args) - self.claimed_dirs
                if new:
                    name = sorted(new)[-1]
                    self.claimed_dirs.add(name)
                    return backup_run_root(self.args) / name
                if process.poll() is not None:
                    return None  # died before creating anything
                time.sleep(0.2)
            return None
        finally:
            self.wait_for_next_second()

    @staticmethod
    def wait_for_next_second():
        now = time.time()
        time.sleep(1.05 - (now % 1.0))

    def consume_output(self, run, raw):
        """Track a run's progress from one piece of its output."""
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return
        match = PROGRESS_RE.search(text)
        if match:
            percent = int(match.group(1))
            if run.percent is None or percent < run.percent:
                run.phase += 1          # a new simulation phase started
                run.phase_started = time.time()
            run.percent = percent
            with self.print_lock:
                self._draw_status()
            return
        match = TOTAL_STEPS_RE.search(text)
        if match:
            run.total_steps = int(match.group(1))
            return
        if self.args.stream:
            self.log("[%d] %s" % (run.index, text))

    def organize(self, run):
        """Move the backup directory into <sweep>/<label>/<repeat>/."""
        if run.backup_dir is None or not Path(run.backup_dir).is_dir():
            return
        if self.args.layout == "raw":
            run.result_dir = run.backup_dir
            return
        destination = self.sweep_dir / run.label / str(run.repeat)
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(run.backup_dir), str(destination))
        run.result_dir = destination

    def execute(self, run):
        if self.stop.is_set():
            run.status = "skipped"
            return
        run.log_path = self.sweep_dir / "logs" / ("%03d_%s_r%02d.log" % (
            run.index, sanitize(run.label), run.repeat))
        run.log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [self.interpreter, TEST_SINGLE.name, run.module_name]
        run.status = "running"
        run.started = time.time()

        with run.log_path.open("wb") as log_file:
            log_file.write(("# %s\n# cwd: %s\n\n" % (
                " ".join(command), COMMON_DIR)).encode())
            log_file.flush()
            with self.launch_lock:
                process = subprocess.Popen(
                    command, cwd=str(COMMON_DIR), env=self.environment(),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.processes[run.index] = process
                self.log("[%d/%d] started %s (repeat %d) -> %s"
                         % (run.index, len(self.runs), run.label, run.repeat,
                            run.log_path.name))
                run.backup_dir = self.claim_new_backup_dir(
                    process, self.args.start_timeout)
                if run.backup_dir is None and process.poll() is None:
                    self.log("[%d/%d] warning: no backup directory appeared "
                             "within %ss; output may be misattributed"
                             % (run.index, len(self.runs),
                                self.args.start_timeout))

            deadline = (run.started + self.args.timeout
                        if self.args.timeout else None)
            # The progress message ends in '\r', not '\n', so read chunks and
            # split on both -- otherwise a whole phase arrives as one line.
            pending = b""
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    break
                log_file.write(chunk)
                log_file.flush()
                pieces = re.split(b"[\r\n]", pending + chunk)
                pending = pieces.pop()
                for piece in pieces:
                    self.consume_output(run, piece)
                if deadline and time.time() > deadline:
                    process.kill()
                    log_file.write(b"\n# killed by run_sweep.py: timeout\n")
                    break
            if pending:
                self.consume_output(run, pending)
            process.wait()

        self.processes.pop(run.index, None)
        run.returncode = process.returncode
        run.finished = time.time()
        if self.stop.is_set() and process.returncode != 0:
            run.status = "interrupted"
        elif process.returncode == 0:
            run.status = "done"
            self.organize(run)
        else:
            run.status = "failed"
            if self.args.layout != "raw":
                run.result_dir = run.backup_dir

        duration = run.finished - run.started
        eta = self.sweep_eta(run.finished)
        done = sum(1 for r in self.runs if r.status == "done")
        self.log("[%d/%d] %s %s (repeat %d) in %s · %d/%d done · sweep %s%s%s"
                 % (run.index, len(self.runs), run.status, run.label, run.repeat,
                    format_duration(duration), done, len(self.runs),
                    format_duration(run.finished - self.started),
                    "" if eta is None else " · ETA %s, done ~%s" % (
                        format_duration(eta),
                        time.strftime("%H:%M",
                                      time.localtime(run.finished + eta))),
                    "" if run.result_dir is None else "\n        -> %s"
                    % run.result_dir))
        if run.status == "failed":
            self.log("        exit code %s, see %s"
                     % (run.returncode, run.log_path))

    def terminate_all(self):
        self.stop.set()
        for process in list(self.processes.values()):
            try:
                process.terminate()
            except OSError:
                pass


def format_duration(seconds):
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "%dh%02dm%02ds" % (hours, minutes, seconds)
    if minutes:
        return "%dm%02ds" % (minutes, seconds)
    return "%ds" % seconds


def write_manifest(path, args, sweep_id, runs, interpreter):
    manifest = {
        "sweep_id": sweep_id,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "param_module": args.param,
        "interpreter": interpreter,
        "repeats": args.repeats,
        "layout": args.layout,
        "command": " ".join(sys.argv),
        "swept": {name: [value_label(v) for v in values]
                  for name, values in args.sweep},
        "constants": {name: value_label(value) for name, value in args.set},
        "exec": list(args.exec_code),
        "runs": [run.as_dict() for run in runs],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(path)


def load_completed(manifest_path):
    """Map (label, repeat) -> result_dir for runs that finished successfully."""
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text())
    except ValueError:
        return {}
    completed = {}
    for record in manifest.get("runs", []):
        if record.get("status") != "done":
            continue
        result_dir = record.get("result_dir")
        if result_dir and Path(result_dir).is_dir():
            completed[(record.get("label"), record.get("repeat"))] = result_dir
    return completed


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--param", default="delpapa.param_FrozenPlasticity",
        help="base parameter module, as given to test_single.py "
             "(default: %(default)s)")
    parser.add_argument(
        "--sweep", action="append", type=sweep_type, metavar="NAME=SPEC",
        help="parameter to sweep; SPEC is start:stop:step (stop included), "
             "a comma separated list, or a single value. Repeat the option to "
             "sweep several parameters (all combinations are run). "
             "Default: h_ip=0.02:0.2:0.02")
    parser.add_argument(
        "--set", action="append", type=set_type, default=[], metavar="NAME=VALUE",
        help="constant override applied to every run, e.g. --set N_steps=100000")
    parser.add_argument(
        "--exec", dest="exec_code", action="append", default=[], metavar="CODE",
        help="python line appended to every generated parameter module, after "
             "the overrides. Use it to recompute derived quantities, e.g. "
             "--exec 'c.N_steps = c.steps_plastic + 2*c.steps_perturbation'. "
             "May be repeated.")
    parser.add_argument(
        "--repeats", type=int, default=1, metavar="N",
        help="simulations per parameter value (default: %(default)s)")
    parser.add_argument(
        "--jobs", type=int, default=1, metavar="N",
        help="simulations to run concurrently; their start-up is serialized so "
             "that each one gets its own backup directory (default: %(default)s)")
    parser.add_argument(
        "--name", default=None,
        help="name of the sweep directory (default: sweep_<param>_<timestamp>)")
    parser.add_argument(
        "--out", default=None, metavar="DIR",
        help="where sweep directories are created (default: <backup-root>/sweeps)")
    parser.add_argument(
        "--backup-root", default=str(DEFAULT_BACKUP_ROOT), metavar="DIR",
        help="backup directory used by test_single.py (default: %(default)s)")
    parser.add_argument(
        "--layout", choices=("organized", "raw"), default="organized",
        help="'organized' moves each result to <sweep>/<label>/<repeat>/, "
             "matching the avalanche scripts; 'raw' keeps the backup "
             "directories (default: %(default)s)")
    parser.add_argument(
        "--python", default=None, metavar="EXE",
        help="interpreter used for test_single.py (default: auto-detected "
             "python 2, since the model code is python 2.7)")
    parser.add_argument(
        "--mpl-backend", default="Agg", metavar="BACKEND",
        help="matplotlib backend for the simulations, via MPLBACKEND; "
             "pass '' to leave it alone (default: %(default)s)")
    parser.add_argument(
        "--timeout", type=float, default=None, metavar="SECONDS",
        help="kill a simulation after this many seconds (default: no limit)")
    parser.add_argument(
        "--start-timeout", type=float, default=300.0, metavar="SECONDS",
        help="how long to wait for a run's backup directory before launching "
             "the next one (default: %(default)s)")
    parser.add_argument(
        "--resume", action="store_true",
        help="reuse an existing sweep directory and skip runs already done")
    parser.add_argument(
        "--keep-params", action="store_true",
        help="keep the generated parameter modules in sweep_params/ "
             "(a copy is archived with every result either way)")
    parser.add_argument(
        "--no-linked-params", dest="link_params", action="store_false",
        help="do not propagate h_ip to c.W_ei.h_ip (see LINKED_PARAMS)")
    parser.add_argument(
        "--progress-interval", type=float, default=None, metavar="SECONDS",
        help="how often the progress line is refreshed; 0 turns it off "
             "(default: 1 on a terminal, 60 when the output is redirected)")
    parser.add_argument(
        "--stream", action="store_true",
        help="mirror simulation output to the console as well as the log")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show the planned runs and generated parameters, run nothing")
    args = parser.parse_args(argv)

    if args.sweep is None:
        args.sweep = [("h_ip", parse_values("0.02:0.2:0.02"))]
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def main(argv=None):
    args = parse_args(argv)

    if not TEST_SINGLE.is_file():
        sys.exit("cannot find %s" % TEST_SINGLE)

    sweep_id = time.strftime("%Y%m%d_%H%M%S")
    base_name = sanitize(args.param.rsplit(".", 1)[-1])
    out_root = Path(args.out) if args.out else Path(args.backup_root) / "sweeps"
    sweep_name = args.name or "sweep_%s_%s" % (base_name, sweep_id)
    sweep_dir = out_root / sweep_name

    if args.resume and args.name is None:
        existing = sorted(p for p in out_root.glob("sweep_%s_*" % base_name)
                          if p.is_dir()) if out_root.is_dir() else []
        if existing:
            sweep_dir = existing[-1]
            print("resuming %s" % sweep_dir)

    runs = build_runs(args, sweep_id)
    completed = load_completed(sweep_dir / "manifest.json") if args.resume else {}

    print("sweep      : %s" % sweep_dir)
    print("base params: %s" % args.param)
    for name, values in args.sweep:
        targets = assignment_targets(name, args.link_params)
        print("sweeping   : %s = %s%s"
              % (name, ", ".join(value_label(v) for v in values),
                 "" if targets == [name] else "  (sets c.%s)"
                 % ", c.".join(targets)))
    for name, value in args.set:
        print("constant   : %s = %s" % (name, value_label(value)))
    for line in args.exec_code:
        print("exec       : %s" % line)
    print("runs       : %d (%d value set(s) x %d repeat(s))"
          % (len(runs), len(runs) // args.repeats, args.repeats))

    if not args.exec_code:
        overridden = [name for name, _ in args.sweep] + [name
                                                         for name, _ in args.set]
        for name in overridden:
            lines = dependent_lines(args.param, name)
            if not lines:
                continue
            print("\nwarning: %s.py computes these from c.%s:" % (
                args.param.rsplit(".", 1)[-1], name))
            for line in lines[:6]:
                print("             %s" % line)
            if len(lines) > 6:
                print("             ... and %d more" % (len(lines) - 6))
            print("         They keep their original value unless you repeat "
                  "them with --exec.")

    interpreter = detect_interpreter(args.python)
    if interpreter is None and not args.dry_run:
        sys.exit(
            "No python 2 interpreter found (looked for: %s).\n"
            "The SORN code in this repository is python 2.7, while this driver "
            "is python 3.\nInstall python 2.7 or point the driver at it with "
            "--python /path/to/python2." % ", ".join(PY2_CANDIDATES))
    if interpreter:
        print("interpreter: %s" % interpreter)
        if args.python and interpreter_major(interpreter) == 3:
            print("warning: %s is python 3, while the SORN code in this "
                  "repository is python 2.7" % interpreter)

    ensure_generated_package()
    sweep_dir.mkdir(parents=True, exist_ok=True)

    pending = []
    for run in runs:
        key = (run.label, run.repeat)
        if key in completed:
            run.status = "done"
            run.result_dir = completed[key]
            run.returncode = 0
            continue
        write_param_module(run, args)
        pending.append(run)

    if args.resume and len(pending) < len(runs):
        print("resuming   : %d of %d runs already done"
              % (len(runs) - len(pending), len(runs)))

    if args.dry_run:
        print("\n--- planned runs ---")
        for run in runs:
            if run.status == "done":
                print("[%d] %s repeat %d: already done (%s)"
                      % (run.index, run.label, run.repeat, run.result_dir))
                continue
            print("[%d] cd %s && %s %s %s"
                  % (run.index, COMMON_DIR, interpreter or "python2",
                     TEST_SINGLE.name, run.module_name))
        if pending:
            print("\n--- %s ---" % pending[0].module_path)
            print(pending[0].module_path.read_text(), end="")
        if not args.keep_params:
            for run in pending:
                remove_generated_module(run.module_path)
        return 0

    manifest_path = sweep_dir / "manifest.json"
    write_manifest(manifest_path, args, sweep_id, runs, interpreter)

    runner = SweepRunner(args, runs, sweep_dir, interpreter)
    started = time.time()
    queue = list(pending)
    queue_lock = threading.Lock()
    signal_count = [0]

    def handle_signal(signum, frame):
        """Stop the sweep from any signal disposition, threads included."""
        signal_count[0] += 1
        if signal_count[0] == 1:
            print("\ninterrupted -- stopping simulations "
                  "(interrupt again to quit immediately)", flush=True)
            runner.terminate_all()
        else:
            os._exit(130)

    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signum] = signal.signal(signum, handle_signal)
        except (ValueError, OSError):  # not the main thread / unsupported
            pass

    def worker():
        while True:
            with queue_lock:
                if not queue or runner.stop.is_set():
                    return
                run = queue.pop(0)
            try:
                runner.execute(run)
            except Exception as error:  # keep the sweep alive
                run.status = "error"
                run.finished = time.time()
                runner.log("[%d] driver error: %s" % (run.index, error))
            finally:
                with queue_lock:
                    write_manifest(manifest_path, args, sweep_id, runs, interpreter)

    threads = [threading.Thread(target=worker)
               for _ in range(min(args.jobs, len(queue) or 1))]
    runner.start_ticker()
    for thread in threads:
        thread.start()
    for thread in threads:
        # join() is interruptible, so signal handlers still run while waiting.
        while thread.is_alive():
            thread.join(timeout=0.5)
    runner.stop_ticker()

    for signum, handler in previous_handlers.items():
        signal.signal(signum, handler)

    if runner.stop.is_set():
        for run in runs:
            if run.status == "running":
                run.status = "interrupted"
        runner.show_status = False
        write_manifest(manifest_path, args, sweep_id, runs, interpreter)
        remaining = [r for r in runs if r.status == "pending"]
        print("\nstopped after %s; %d run(s) not started"
              % (format_duration(time.time() - started), len(remaining)))
        print("results  : %s" % sweep_dir)
        print("rerun with --resume --name %s to continue" % sweep_dir.name)
        return 130

    if not args.keep_params:
        for run in pending:
            remove_generated_module(run.module_path)

    write_manifest(manifest_path, args, sweep_id, runs, interpreter)

    done = [r for r in runs if r.status == "done"]
    failed = [r for r in runs if r.status not in ("done", "pending")]
    timed = [r for r in done if r.started and r.finished]
    print("\nfinished %d/%d runs in %s%s"
          % (len(done), len(runs), format_duration(time.time() - started),
             "" if not timed else " (%s per run on average)" % format_duration(
                 sum(r.finished - r.started for r in timed) / len(timed))))
    print("results  : %s" % sweep_dir)
    print("manifest : %s" % manifest_path)
    if failed:
        print("failed   : %s"
              % ", ".join("%s r%d (%s)" % (r.label, r.repeat, r.status)
                          for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
