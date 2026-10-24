import io

from infra.lifecycle import ShutdownController, watch_stdin


class FakeServer:
    should_exit = False


def test_request_marks_attached_servers_for_exit():
    controller = ShutdownController()
    server = FakeServer()
    controller.attach(server)
    assert controller.request("测试") is True
    assert server.should_exit is True
    assert controller.reason == "测试"


def test_server_attached_after_request_is_marked_immediately():
    controller = ShutdownController()
    controller.request("先")
    server = FakeServer()
    controller.attach(server)
    assert server.should_exit is True


def test_repeated_request_is_ignored_and_keeps_first_reason():
    controller = ShutdownController()
    assert controller.request("第一次") is True
    assert controller.request("第二次") is False
    assert controller.reason == "第一次"


def test_stdin_shutdown_command_requests_exit():
    reasons: list[str] = []
    thread = watch_stdin(io.StringIO("hello\nshutdown\n"), lambda r: reasons.append(r) or True)
    thread.join(5)
    assert reasons == ["stdin 指令"]


def test_stdin_eof_requests_exit():
    reasons: list[str] = []
    thread = watch_stdin(io.StringIO("some text\n"), lambda r: reasons.append(r) or True)
    thread.join(5)
    assert reasons == ["stdin 已关闭"]
