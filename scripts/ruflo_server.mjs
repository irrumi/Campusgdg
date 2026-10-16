// Anchor the shared Ruflo MCP process to the coordinator's project directory.
import { spawn } from 'node:child_process';
const [project, entrypoint] = process.argv.slice(2);
if (!project || !entrypoint) throw new Error('Expected project directory and installed Ruflo entrypoint');
const server = spawn(process.execPath, [entrypoint, 'mcp', 'start'], {
  cwd: project, stdio: 'inherit', windowsHide: true,
});
server.on('exit', code => process.exit(code ?? 1));
server.on('error', error => { console.error(error.message); process.exit(1); });
