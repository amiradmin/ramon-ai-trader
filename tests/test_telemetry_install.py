import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT=Path(__file__).parents[1]


@pytest.mark.parametrize("compile_ok",[True,False])
def test_installer_backs_up_before_compile_and_restarts_only_after_success(tmp_path,compile_ok):
    install=tmp_path/".mt5"/"drive_c"/"Program Files"/"MetaTrader 5"
    (install/"MQL5"/"Experts").mkdir(parents=True)
    (install/"MetaEditor64.exe").touch()
    executables=tmp_path/"bin"; executables.mkdir()
    log=tmp_path/"calls.jsonl"
    docker=executables/"docker"
    docker.write_text("#!/usr/bin/env python3\nimport sys,json,os\nwith open(os.environ['CALL_LOG'],'a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n")
    winepath=executables/"winepath"
    winepath.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$2"\n')
    wine=executables/"wine"
    if compile_ok:
        wine.write_text('#!/usr/bin/env bash\nset -euo pipefail\nsource_file="${2#/compile:}"\nprintf "fake compile for script test\\n" > "${source_file%.mq5}.ex5"\n')
    else:
        wine.write_text('#!/usr/bin/env bash\nexit 72\n')
    for tool in (docker,wine,winepath):
        tool.chmod(0o755)
    env={**os.environ,"HOME":str(tmp_path),"PATH":str(executables)+os.pathsep+os.environ["PATH"],
         "CALL_LOG":str(log),"RAMON_MT5_DIR":str(install),"RAMON_MT5_DATA_DIR":str(install),
         "RAMON_WINEPREFIX":str(tmp_path/".mt5")}
    result=subprocess.run(["bash",str(ROOT/"scripts/install_research_telemetry.sh")],
                          env=env,capture_output=True,text=True)
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert "backup" in calls[0]
    assert calls[0][-2]=="--out"
    assert calls[0][-1].startswith("/data/backups/pre-telemetry-")
    if compile_ok:
        assert result.returncode==0,result.stderr
        assert len(calls)==2
        assert calls[1]==["compose","-f","compose.yaml","-f","compose.telemetry.yaml",
                          "up","-d","--no-deps","--no-build","--force-recreate","model"]
    else:
        assert result.returncode!=0
        assert len(calls)==1


def test_override_keeps_model_source_readonly_and_does_not_change_worker():
    override=yaml.safe_load((ROOT/"compose.telemetry.yaml").read_text())
    assert set(override["services"])=={"model"}
    assert override["services"]["model"]["environment"]=={"PYTHONPATH":"/research-src"}
    assert override["services"]["model"]["volumes"]==["./src:/research-src:ro"]

