import base64

from heavybag.ssh import Ssh, encode_script


def test_encode_script_round_trips() -> None:
    script = "echo 'hi $there'\nexit 3\n"
    wrapped = encode_script(script)
    assert wrapped.startswith("printf %s ") and wrapped.endswith(" | base64 -d | sh")
    payload = wrapped.split()[2]
    assert base64.b64decode(payload).decode() == script


def test_extra_args_are_part_of_the_transport(monkeypatch) -> None:
    monkeypatch.setenv("HEAVYBAG_NO_CONTROL_MASTER", "1")
    ssh = Ssh("gpu-box", ["-p", "2222"])
    args = ssh.command_args("true")
    assert args[0] == "ssh" and args[-2:] == ["gpu-box", "true"]
    assert "-p" in args and "2222" in args
    assert "ServerAliveInterval=10" in args
    assert ssh.rsync_transport().endswith("-p 2222")
