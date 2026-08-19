from src.engine.engine_core import EngineCore


def test_engine_core_reuses_state_per_symbol() -> None:
    engine = EngineCore()
    assert engine.bootstrap("SPX") is engine.bootstrap("SPX")

