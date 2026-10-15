from AlgorithmImports import *
from datetime import timedelta
import json
import math


class Mom121FreeCloudDataAcceptanceAudit(QCAlgorithm):
    """Data-access pilot only. It does not trade or report strategy performance."""

    CAP_CEILING_USD = 300_000_000
    MIN_PRICE_USD = 1.00
    COMMON_STOCK_CODE = "ST00000001"
    EXCHANGE_CODES_TO_REVIEW = {"NYS", "NAS", "ASE"}

    def initialize(self):
        # QuantConnect's documented US fundamental/security-master cloud history
        # starts in 1998. End on a completed month for deterministic inspection.
        self.set_start_date(1998, 1, 1)
        self.set_end_date(2026, 8, 31)
        self.set_cash(1_000_000)
        self.set_benchmark("SPY")

        self._audit_complete = False
        self._event_evidence = []
        self._case_tickers = {"LEH", "TWTR", "FB", "META"}

        # These low-volume subscriptions exist only to observe sample delisting
        # and ticker-map events. They are not MOM121 portfolio constituents.
        self.add_equity("SPY", Resolution.DAILY)
        for ticker in ("LEH", "TWTR", "FB"):
            self.add_equity(
                ticker,
                Resolution.DAILY,
                data_normalization_mode=DataNormalizationMode.RAW
            )

        # Take one historical fundamental snapshot after the feeds have started.
        self.schedule.on(
            self.date_rules.on(1999, 1, 4),
            self.time_rules.after_market_open("SPY", 60),
            self.audit_fundamental_snapshot
        )

    def audit_fundamental_snapshot(self):
        if self._audit_complete:
            return
        self._audit_complete = True

        # This history API is documented to return US Fundamentals objects,
        # including delisted companies. Only aggregate field-presence counts
        # and classifications are written to the log; no source rows are exported.
        try:
            records = list(self.history[Fundamentals](timedelta(days=7)))
        except Exception as exc:
            self.debug(json.dumps({
                "experiment_id": "MOM121-FREE-CLOUD-DATA-AUDIT-001",
                "status": "technical_failure",
                "stage": "historical_fundamentals_history",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "strategy_metrics_emitted": False
            }, sort_keys=True))
            return

        # Select the most recent available observation per stable Symbol in the
        # requested lookback, not the last row returned by iteration order.
        latest = {}
        for f in records:
            try:
                symbol = f.symbol
                end_time = f.end_time
                if end_time > self.time:
                    continue
                old = latest.get(symbol)
                if old is None or end_time > old.end_time:
                    latest[symbol] = f
            except Exception:
                continue

        security_type_counts = {}
        share_status_counts = {}
        exchange_counts = {}
        price_present = 0
        cap_present = 0
        common_price_cap = 0
        common_price_cap_primary_exchange = 0
        active_status_count = 0
        inactive_or_delisted_status_count = 0
        field_access_errors = 0

        for f in latest.values():
            try:
                ref = f.security_reference
                security_type = str(ref.security_type or "MISSING").strip()
                share_status = str(ref.share_class_status or "MISSING").strip()
                exchange = str(ref.exchange_id or "MISSING").strip()
                security_type_counts[security_type] = security_type_counts.get(security_type, 0) + 1
                share_status_counts[share_status] = share_status_counts.get(share_status, 0) + 1
                exchange_counts[exchange] = exchange_counts.get(exchange, 0) + 1

                try:
                    price = float(f.price)
                    price_ok = math.isfinite(price) and price >= self.MIN_PRICE_USD
                except Exception:
                    price_ok = False
                try:
                    cap = float(f.market_cap)
                    cap_ok = math.isfinite(cap) and 0 < cap <= self.CAP_CEILING_USD
                except Exception:
                    cap_ok = False

                price_present += int(price_ok)
                cap_present += int(cap_ok)
                common = security_type == self.COMMON_STOCK_CODE
                if common and price_ok and cap_ok:
                    common_price_cap += 1
                    if exchange in self.EXCHANGE_CODES_TO_REVIEW:
                        common_price_cap_primary_exchange += 1

                if share_status.lower() == "active":
                    active_status_count += 1
                if share_status.lower() in {"deactive", "inactive", "obsolete"}:
                    inactive_or_delisted_status_count += 1
            except Exception:
                field_access_errors += 1

        summary = {
            "experiment_id": "MOM121-FREE-CLOUD-DATA-AUDIT-001",
            "status": "snapshot_observed_needs_review" if latest else "blocked_data_gate",
            "snapshot_algorithm_time": str(self.time),
            "history_lookback_days": 7,
            "history_objects_returned": len(records),
            "unique_symbols_with_observations": len(latest),
            "price_at_least_1_usd_count": price_present,
            "market_cap_positive_and_at_most_300m_count": cap_present,
            "common_stock_code": self.COMMON_STOCK_CODE,
            "security_type_counts": security_type_counts,
            "share_class_status_counts": share_status_counts,
            "exchange_id_counts": exchange_counts,
            "common_stock_price_and_cap_eligible_count": common_price_cap,
            "eligible_count_using_exchange_codes_requiring_review": common_price_cap_primary_exchange,
            "active_share_class_count": active_status_count,
            "inactive_or_delisted_status_count": inactive_or_delisted_status_count,
            "field_access_errors": field_access_errors,
            "representative_event_evidence_seen_so_far": len(self._event_evidence),
            "strategy_metrics_emitted": False,
            "raw_data_exported": False,
            "limitations": [
                "One historical snapshot does not establish full-universe point-in-time coverage.",
                "Counts are diagnostic only; exchange-code mapping must be independently checked.",
                "A delisting event alone does not establish correct terminal proceeds or delisting returns.",
                "No MOM121 portfolio or performance backtest is executed by this pilot."
            ]
        }
        self.debug("MOM121_DATA_AUDIT_JSON=" + json.dumps(summary, sort_keys=True))

    def on_data(self, data: Slice):
        # Capture only symbolic corporate-action event metadata, never raw data.
        try:
            for symbol, event in data.delistings.items():
                ticker = getattr(symbol, "value", str(symbol))
                if ticker in self._case_tickers:
                    row = {
                        "kind": "delisting_event",
                        "ticker": ticker,
                        "time": str(self.time),
                        "event_type": str(getattr(event, "type", "unknown"))
                    }
                    self._event_evidence.append(row)
                    self.debug("MOM121_CORPORATE_ACTION_AUDIT=" + json.dumps(row, sort_keys=True))
        except Exception:
            pass

        try:
            for _, event in data.symbol_changed_events.items():
                old_ticker = str(getattr(getattr(event, "old_symbol", None), "value", ""))
                new_ticker = str(getattr(getattr(event, "new_symbol", None), "value", ""))
                if {old_ticker, new_ticker} & self._case_tickers:
                    row = {
                        "kind": "symbol_changed_event",
                        "old_ticker": old_ticker,
                        "new_ticker": new_ticker,
                        "time": str(self.time)
                    }
                    self._event_evidence.append(row)
                    self.debug("MOM121_CORPORATE_ACTION_AUDIT=" + json.dumps(row, sort_keys=True))
        except Exception:
            pass

    def on_end_of_algorithm(self):
        self.debug("MOM121_AUDIT_TERMINAL=" + json.dumps({
            "experiment_id": "MOM121-FREE-CLOUD-DATA-AUDIT-001",
            "snapshot_observed": self._audit_complete,
            "corporate_action_events_observed": len(self._event_evidence),
            "status": "audit_only_no_strategy_backtest",
            "strategy_metrics_emitted": False,
            "raw_data_exported": False
        }, sort_keys=True))
