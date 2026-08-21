import subprocess
result = subprocess.run(
    ['python', '-m', 'pytest', 'tests/test_phase5_evals.py', '--collect-only'],
    capture_output=True, text=True, cwd='C:\\Users\\pc\\Desktop\\OPS-ch\\backend'
)
print(result.stdout)
print(result.stderr)