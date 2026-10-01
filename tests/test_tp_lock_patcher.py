from pathlib import Path
import importlib.util


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_tp_lock_to_installed_ea.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tp_patcher", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def base_source(existing_on_tick: str) -> str:
    return f'''#property version "1.537"

string TPStageLockStatus = "INACTIVE";

bool ProtectReachedTPStage(const ulong ticket)
{{
   return true;
}}

void ObserveTPStageCrossingsOnTick()
{{
}}

{existing_on_tick}

void OnTimer()
{{
}}
'''


def test_patcher_merges_observer_into_existing_ontick_without_duplicate():
    module = load_module()
    source = base_source(
        """void OnTick()
{
   ExistingLocalTickLogic();
}
"""
    ) + module.PATCH_ONTICK

    result = module.patch_source(source)

    assert result.count("void OnTick()") == 1
    assert "ExistingLocalTickLogic();" in result
    start = result.index("void OnTick()")
    end = result.index("void OnTimer()")
    assert result[start:end].count("ObserveTPStageCrossingsOnTick();") == 1


def test_patcher_is_idempotent():
    module = load_module()
    source = base_source(
        """void OnTick()
{
   ExistingLocalTickLogic();
}
"""
    )

    first = module.patch_source(source)
    second = module.patch_source(first)

    assert first == second
    assert second.count("void OnTick()") == 1


def test_patcher_rejects_ambiguous_duplicate_local_ontick():
    module = load_module()
    source = base_source(
        """void OnTick()
{
}
"""
    ) + """
void OnTick()
{
   OtherLogic();
}
"""

    try:
        module.patch_source(source)
    except ValueError as exc:
        assert "exactly one existing OnTick" in str(exc)
    else:
        raise AssertionError("duplicate OnTick should be rejected")
