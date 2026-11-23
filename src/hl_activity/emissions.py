"""Protocol issuance of the gas asset.

Emissions are new units the protocol creates: a block subsidy, an inflation
schedule, or a mint module's annual provisions. They are not reward-claim
transactions, which only move coins that were already issued. NEV is fees
minus emissions, both in USD per minute. If either side was not measured,
NEV is null rather than treating the gap as zero.
"""

YEAR_SECONDS = 365.25 * 24 * 3600


def over_window(amount_per_year, window_seconds):
    if amount_per_year is None or window_seconds is None or window_seconds <= 0:
        return None
    return amount_per_year / YEAR_SECONDS * window_seconds


def attach_annual(sample, amount_per_year, window_seconds, basis):
    window = window_seconds if window_seconds and window_seconds > 0 else 60.0
    attach_emissions(sample, over_window(amount_per_year, window), window, basis)


def attach_emissions(sample, native_amount, window_seconds, basis):
    sample["emissions_native"] = native_amount
    extra = sample.setdefault("extra", {})
    if window_seconds is not None:
        extra["emissions_window_seconds"] = window_seconds
    extra["emissions_basis"] = basis
