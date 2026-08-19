from src.bot.basket_selector import StaticBasketSelector
from src.models.basket import BasketRequest


def test_default_basket_selection() -> None:
    selector = StaticBasketSelector.from_config("configs/baskets_v1.json")
    request = BasketRequest(symbol="CURRENT_UNIVERSE", start="2023-01-01", end_exclusive="2026-06-23")
    selection = selector.select_basket("2026-06-22", request)

    assert selection.basket.name == "CURRENT_UNIVERSE"
    assert selection.basket.symbols[0] == "SPX"
    assert selection.request.symbol == "CURRENT_UNIVERSE"
