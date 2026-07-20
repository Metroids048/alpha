"""Field catalog independent of pipeline orchestration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from alpha_mining.common import to_float
from .operator_registry import BASE_VARS


def is_bad_field_name(field_name: str) -> bool:
    low = str(field_name or "").lower()
    if not low or low in BASE_VARS:
        return False
    tokens = (
        "currency",
        "country",
        "zipcode",
        "address",
        "url",
        "phone",
        "email",
        "name",
        "description",
        "ticker",
        "cusip",
        "isin",
        "sedol",
        "exchange",
        "sector_code",
        "industry_code",
        "date",
        "year",
        "quarter",
        "flag",
    )
    return any(token in low for token in tokens) or low.endswith(
        ("_id", "_code", "_cd", "_key")
    )


def is_weak_fundamental_field(field_name: str) -> bool:
    low = str(field_name or "").lower()
    weak = (
        "receivable",
        "inventory",
        "payable",
        "income_tax",
        "liabilit",
        "xidoc",
        "xrent",
        "txbco",
        "optca",
        "cld",
        "recd",
        "mrc1",
        "rdipa",
        "rent",
        "tax",
    )
    return (
        not low
        or any(token in low for token in weak)
        or (
            "guidance" in low
            and not any(
                x in low for x in ("ebitda", "eps", "sales", "revenue", "profit")
            )
        )
    )


def field_quality_score(field_name: str) -> float:
    low = str(field_name or "").lower()
    good = (
        "sales",
        "revenue",
        "income",
        "profit",
        "ebit",
        "ebitda",
        "cashflow",
        "free_cash",
        "assets",
        "asset",
        "liabilities",
        "debt",
        "equity",
        "book",
        "margin",
        "expense",
        "capex",
        "accrual",
        "inventory",
        "receivable",
        "payable",
        "dividend",
        "shares",
        "return_equity",
        "operating",
        "eps",
    )
    score = sum(1.0 for token in good if token in low)
    score += 0.5 if "estimate" in low or "guidance" in low else 0.0
    score += 0.25 if low.startswith(("mdf_", "fnd6_", "fn_", "fundamental")) else 0.0
    return min(score, 5.0)


@dataclass
class FieldCatalog:
    df: Any
    ids: set[str]
    by_ds: dict[str, list[str]]
    fund: list[str]
    analyst: list[str]
    model: list[str]
    sent: list[str]
    pv: list[str]
    other: list[str]
    base_vars: set[str] = field(default_factory=lambda: set(BASE_VARS))
    field_dataset: dict[str, str] = field(default_factory=dict)
    field_user_count: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_df(cls, df: Any) -> "FieldCatalog":
        ids: set[str] = set()
        by_ds: dict[str, list[str]] = defaultdict(list)
        pools: dict[str, list[str]] = {
            name: [] for name in ("fund", "analyst", "model", "sent", "pv", "other")
        }
        datasets: dict[str, str] = {}
        users: dict[str, float] = {}
        for _, row in df.iterrows():
            fid, ds = str(row.get("id", "")).strip(), str(row.get("_ds", "")).lower()
            if not fid:
                continue
            ids.add(fid)
            by_ds[ds].append(fid)
            datasets[fid] = ds
            users[fid] = to_float(row.get("userCount")) or 0.0
            low = fid.lower()
            if "fundamental" in ds or any(
                x in low
                for x in (
                    "sales",
                    "revenue",
                    "income",
                    "profit",
                    "asset",
                    "debt",
                    "cash",
                    "eps",
                )
            ):
                pool = "fund"
            elif "analyst" in ds or any(
                x in low for x in ("analyst", "estimate", "forecast", "revision")
            ):
                pool = "analyst"
            elif "model" in ds or low.startswith("mdl"):
                pool = "model"
            elif any(x in ds for x in ("sentiment", "news", "social")):
                pool = "sent"
            elif (
                ds.startswith("pv")
                or fid in BASE_VARS
                or any(x in low for x in ("close", "volume", "vwap", "return", "price"))
            ):
                pool = "pv"
            else:
                pool = "other"
            pools[pool].append(fid)
        return cls(
            df=df,
            ids=ids,
            by_ds=dict(by_ds),
            fund=list(dict.fromkeys(pools["fund"])),
            analyst=list(dict.fromkeys(pools["analyst"])),
            model=list(dict.fromkeys(pools["model"])),
            sent=list(dict.fromkeys(pools["sent"])),
            pv=list(dict.fromkeys(pools["pv"])),
            other=list(dict.fromkeys(pools["other"])),
            field_dataset=datasets,
            field_user_count=users,
        )

    def best(
        self, pools: Iterable[list[str]], tokens: tuple[str, ...] = ()
    ) -> str | None:
        fields = list(
            dict.fromkeys(
                f for pool in pools for f in pool if f and not is_bad_field_name(f)
            )
        )
        fields.sort(
            key=lambda f: (
                sum(token in f.lower() for token in tokens),
                field_quality_score(f),
                f,
            ),
            reverse=True,
        )
        return fields[0] if fields else None

    def replacement_for(self, field_name: str) -> str | None:
        pool = next(
            (
                p
                for p in (
                    self.fund,
                    self.analyst,
                    self.model,
                    self.sent,
                    self.pv,
                    self.other,
                )
                if field_name in p
            ),
            [],
        )
        choices = [
            f
            for f in pool
            if f != field_name
            and self.field_dataset.get(f) != self.field_dataset.get(field_name)
        ]
        choices.sort(
            key=lambda f: (
                field_quality_score(f),
                -self.field_user_count.get(f, 0.0),
                f,
            ),
            reverse=True,
        )
        return choices[0] if choices else None
