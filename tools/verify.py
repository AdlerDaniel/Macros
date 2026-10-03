from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
binary = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist'/'Macros.exe'
output = root/'artifacts'/'packaged'
output.mkdir(parents=True,exist_ok=True)
(output/'self-test.json').unlink(missing_ok=True)
result = subprocess.run([str(binary),'--self-test',str(output)],timeout=90)
report = json.loads((output/'self-test.json').read_text('utf-8'))
print(json.dumps({'exe':str(binary),'exit_code':result.returncode,'success':report.get('success'),'checks':report.get('passed'),'error':report.get('error')},ensure_ascii=False,indent=2))
if result.returncode or not report.get('success'):
    raise SystemExit(1)
