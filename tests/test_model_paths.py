from __future__ import annotations

import json
from pathlib import Path

import pytest

from ramon.model import model_name


def test_model_name_accepts_supported_hub_models() -> None:
    assert model_name("autogluon/chronos-2-small") == "autogluon/chronos-2-small"
    assert model_name("amazon/chronos-2") == "amazon/chronos-2"


def test_model_name_accepts_full_local_checkpoint(tmp_path: Path) -> None:
    checkpoint = tmp_path / "full-model"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")

    assert model_name(str(checkpoint)) == str(checkpoint.resolve())


def test_model_name_accepts_chronos_lora_adapter(tmp_path: Path) -> None:
    checkpoint = tmp_path / "adapter"
    checkpoint.mkdir()
    (checkpoint / "adapter_config.json").write_text(
        json.dumps(
            {
                "peft_type": "LORA",
                "base_model_name_or_path": "autogluon/chronos-2-small",
            }
        ),
        encoding="utf-8",
    )
    (checkpoint / "adapter_model.safetensors").write_bytes(b"test weights")

    assert model_name(str(checkpoint)) == str(checkpoint.resolve())


@pytest.mark.parametrize(
    "config,weights",
    [
        (None, False),
        ({"peft_type": "LORA", "base_model_name_or_path": "unknown/model"}, True),
        ({"peft_type": "IA3", "base_model_name_or_path": "autogluon/chronos-2-small"}, True),
    ],
)
def test_model_name_rejects_incomplete_or_untrusted_adapter(
    tmp_path: Path,
    config: dict[str, str] | None,
    weights: bool,
) -> None:
    checkpoint = tmp_path / "bad-adapter"
    checkpoint.mkdir()
    if config is not None:
        (checkpoint / "adapter_config.json").write_text(json.dumps(config), encoding="utf-8")
    if weights:
        (checkpoint / "adapter_model.safetensors").write_bytes(b"test weights")

    with pytest.raises(ValueError):
        model_name(str(checkpoint))
