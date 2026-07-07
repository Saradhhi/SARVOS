import os
import subprocess
import shlex
import time
import sys
class DeveloperTools:
    def __init__(self, config):
        self.config = config
        self.workspace = config.get('allowed_workspace_path', './workspace')
    def analyze_workspace(self) -> str:
        tree = []
        for root, dirs, files in os.walk(self.workspace):
            level = root.replace(self.workspace, '').count(os.sep)
            indent = ' ' * 4 * level
            tree.append(f'{indent}{os.path.basename(root)}/')
            subindent = ' ' * 4 * (level + 1)
            for f_name in files:
                tree.append(f'{subindent}{f_name}')
        return '\n'.join(tree)
    def read_file(self, relative_path: str) -> str:
        safe_path = os.path.abspath(os.path.join(self.workspace, relative_path))
        if not safe_path.startswith(os.path.abspath(self.workspace)):
            raise PermissionError('Access denied: Path outside workspace bounds.')
        if os.path.exists(safe_path):
            with open(safe_path, 'r', encoding='utf-8') as f_file:
                return f_file.read()
        return f'File {relative_path} not found.'
    def write_file(self, relative_path: str, content: str) -> str:
        safe_path = os.path.abspath(os.path.join(self.workspace, relative_path))
        if not safe_path.startswith(os.path.abspath(self.workspace)):
            raise PermissionError('Access denied: Target path outside workspace bounds.')
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, 'w', encoding='utf-8') as f_file:
            f_file.write(content)
        return f'Successfully wrote to {relative_path}.'
    def run_tests(self) -> tuple[int, str]:
        cmd = self.config.get('test_command', 'pytest')
        return self._execute_cmd(cmd)
    def execute_deployment(self) -> tuple[int, str]:
        cmd = self.config.get('deploy_command', 'echo Deploy finished')
        return self._execute_cmd(cmd)
    def check_runtime_health(self) -> str:
        log_path = self.config.get('telemetry_log_path')
        if log_path and os.path.exists(log_path):
            with open(log_path, 'r') as f_file:
                lines = f_file.readlines()
                return ''.join(lines[-20:])
        return 'No diagnostic health files found. Process active.'
    def _execute_cmd(self, command_str: str) -> tuple[int, str]:
        try:
            use_shell = sys.platform.startswith('win')
            if use_shell:
                result = subprocess.run(command_str, capture_output=True, text=True, timeout=120, shell=True)
            else:
                args = shlex.split(command_str)
                result = subprocess.run(args, capture_output=True, text=True, timeout=120, shell=False)
            return result.returncode, result.stdout + '\n' + result.stderr
        except Exception as e:
            return -1, str(e)
