"""Offline verifier namespaces must still resolve their own loopback host."""

from pathlib import Path

from polaris.cells.factory.pipeline.tests.test_native_validation_group import _group, _run, _write


def test_verifier_can_resolve_and_listen_on_localhost(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    workspace.mkdir()
    _write(
        workspace,
        "loopback.js",
        "const net = require('node:net');\n"
        "const deadline = setTimeout(() => { console.error('localhost unavailable'); process.exit(1); }, 5000);\n"
        "const server = net.createServer();\n"
        "server.on('error', error => { clearTimeout(deadline); console.error(error); process.exit(1); });\n"
        "server.listen(0, 'localhost', () => {\n"
        "  console.log('LOCALHOST_READY:' + server.address().address);\n"
        "  server.close(() => clearTimeout(deadline));\n"
        "});\n",
    )
    with _group(workspace, tmp_path) as group:
        result = _run(group, ["node", "loopback.js"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LOCALHOST_READY:127.0.0.1" in result.stdout
    assert not (workspace / "system").exists()
