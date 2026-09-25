import pytest

from scrobbler import worker


@pytest.fixture
def tasks(app):
    """An empty task registry for the test, restored afterwards."""
    saved = dict(worker._tasks)
    worker._tasks.clear()
    yield worker
    worker._tasks.clear()
    worker._tasks.update(saved)


def test_imports_are_registered_by_the_app(app):
    assert {"imports", "imports.requeue_stalled"} <= set(worker.registered())


def test_queue_tasks_run_until_there_is_no_work(tasks, metric_delta):
    work = [3]

    def drain():
        if work[0] == 0:
            return False
        work[0] -= 1
        return True

    worked = metric_delta("scrobbler_worker_tasks_total", task="drain", outcome="worked")
    tasks.register_task("drain", drain)
    tasks.run(once=True)
    assert work == [0]
    assert worked.delta == 3


def test_periodic_tasks_keep_their_interval(tasks):
    calls = []
    tasks.register_periodic("tick", 3600, lambda: calls.append(1))
    busy = [2]
    tasks.register_task("busy", lambda: busy.__setitem__(0, busy[0] - 1) or busy[0] >= 0)
    tasks.run(once=True)  # several passes while "busy" has work
    assert calls == [1]  # ran at start, then not again within the hour


def test_a_failing_task_does_not_stop_the_others(tasks, metric_delta):
    done = []

    def broken():
        raise RuntimeError("bug")

    errors = metric_delta("scrobbler_worker_tasks_total", task="broken", outcome="error")
    tasks.register_task("broken", broken)
    tasks.register_task("fine", lambda: done.append(1) and False)
    tasks.run(once=True)
    assert done == [1]
    assert errors.delta == 1


def test_worker_cli_lists_and_runs_tasks(app, tasks):
    ran = []
    tasks.register_task("cli-test", lambda: ran.append(1) and False)
    result = app.test_cli_runner().invoke(args=["worker", "--once", "--no-metrics"])
    assert result.exit_code == 0, result.output
    assert ran == [1]
    alias = app.test_cli_runner().invoke(args=["imports", "worker", "--once", "--no-metrics"])
    assert alias.exit_code == 0, alias.output
