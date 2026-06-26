"""
Risk Engine for Position Sizing and TP/SL Calculations.
Supports four risk types: fixed_pct, fixed_usd, adjustable_pct, and adjustable_usd.
"""

import math
from dataclasses import dataclass
from backend.core.logger import log, log_sync

@dataclass
class OrderParams:
    entry_price: float
    sl_price: float
    tp_price: float
    quantity: float
    position_size_usd: float
    leverage: float
    direction: str
    risk_amount_usd: float

class RiskEngine:
    @staticmethod
    def _get_margin_pct(account_config: dict):
        margin_pct = account_config.get("margin_pct")
        if margin_pct is not None:
            try:
                return float(margin_pct)
            except (TypeError, ValueError):
                return None

        margin_cfg = account_config.get("margin", {})
        if isinstance(margin_cfg, dict):
            for key in ("pct", "percent", "value", "default"):
                value = margin_cfg.get(key)
                if value is None:
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def validate_risk_input(account_config: dict, risk_value: float) -> bool:
        """
        Validates the risk value against the boundaries defined in the account's risk config.
        """
        risk_cfg = account_config.get("risk_config", {})
        risk_type = risk_cfg.get("type")
        
        if risk_type in ("fixed_pct", "fixed_usd"):
            # Fixed risk options are not adjustable on UI, so validate against exact configured value
            return risk_value == float(risk_cfg.get("value", 0.0))
            
        elif risk_type == "adjustable_pct":
            min_val = float(risk_cfg.get("min", 0.5))
            max_val = float(risk_cfg.get("max", 3.0))
            step = float(risk_cfg.get("step", 0.5))
            
            if risk_value < min_val or risk_value > max_val:
                return False
                
            # Check step resolution
            steps = round((risk_value - min_val) / step, 6)
            return steps.is_integer()
            
        elif risk_type == "adjustable_usd":
            min_val = float(risk_cfg.get("min", 10.0))
            max_val = float(risk_cfg.get("max", 200.0))
            step = float(risk_cfg.get("step", 10.0))
            
            if risk_value < min_val or risk_value > max_val:
                return False
                
            steps = round((risk_value - min_val) / step, 6)
            return steps.is_integer()
            
        return False

    @staticmethod
    def calculate_order(
        account_config: dict,
        entry_price: float,
        sl_price: float,
        direction: str,
        rr: float,
        balance: float,
        risk_override: float = None
    ) -> OrderParams:
        """
        Computes risk-aligned order parameters including position size, quantity, leverage, and TP.
        Handles bounds checks, leverage caps, and position size limits.
        """
        direction = direction.upper()
        risk_cfg = account_config.get("risk_config", {})
        risk_type = risk_cfg.get("type")
        max_leverage = float(account_config.get("max_leverage", 10.0))
        max_pos_usd = float(account_config.get("max_position_size_usd", 5000.0))
        margin_pct = RiskEngine._get_margin_pct(account_config)
        margin_budget_usd = balance * (margin_pct / 100.0) if margin_pct is not None and balance > 0 else balance
        
        # 1. Determine Risk Amount in USD
        risk_value = risk_override if risk_override is not None else float(risk_cfg.get("value", 0.0))
        
        if risk_override is None:
            # If no override and adjustable, use default
            if risk_type == "adjustable_pct":
                risk_value = float(risk_cfg.get("default", 1.0))
            elif risk_type == "adjustable_usd":
                risk_value = float(risk_cfg.get("default", 50.0))
                
        # Calculate risk in USD
        if risk_type in ("fixed_pct", "adjustable_pct"):
            risk_pct = risk_value
            risk_amount_usd = balance * (risk_pct / 100.0)
        else:
            # fixed_usd or adjustable_usd
            risk_amount_usd = risk_value
            
        # Avoid division by zero
        if entry_price <= 0:
            raise ValueError(f"Entry price must be positive: {entry_price}")
            
        # 2. SL Distance
        sl_distance_pct = abs(entry_price - sl_price) / entry_price
        if sl_distance_pct == 0:
            raise ValueError("SL price cannot equal Entry price")
            
        # 3. Raw Position Size (USDT)
        position_size_usd = risk_amount_usd / sl_distance_pct
        
        # 4. Cap by max_position_size_usd
        if position_size_usd > max_pos_usd:
            log_sync(f"RiskEngine: Capping position size ${position_size_usd:.2f} by max limit ${max_pos_usd:.2f}")
            position_size_usd = max_pos_usd
            risk_amount_usd = position_size_usd * sl_distance_pct  # Recalculate actual risk
            
        # 5. Cap by max_leverage
        leverage_base = margin_budget_usd if margin_budget_usd > 0 else balance
        calculated_leverage = position_size_usd / leverage_base if leverage_base > 0 else max_leverage
        if calculated_leverage > max_leverage:
            log_sync(
                f"RiskEngine: Calculated leverage {calculated_leverage:.2f}x exceeds limit {max_leverage:.2f}x. "
                f"Capping against margin budget ${leverage_base:.2f}."
            )
            position_size_usd = leverage_base * max_leverage
            risk_amount_usd = position_size_usd * sl_distance_pct  # Recalculate actual risk
            leverage = max_leverage
        else:
            leverage = float(max(1, math.ceil(calculated_leverage)))
            
        # 6. Quantity in coin
        quantity = position_size_usd / entry_price
        
        # 7. Recalculate Take Profit based on R:R
        # LONG TP = entry_price + sl_dist * RR
        # SHORT TP = entry_price - sl_dist * RR
        sl_diff = abs(entry_price - sl_price)
        if direction == "LONG":
            tp_price = entry_price + (sl_diff * rr)
        else:
            tp_price = entry_price - (sl_diff * rr)
            
        log_sync(
            f"RiskEngine: Calculated Order -> Sizing: ${position_size_usd:.2f} ({quantity:.6f} units), "
            f"Leverage: {leverage:.2f}x, Margin budget: ${margin_budget_usd:.2f}"
            + (f" ({margin_pct:.2f}% of balance)" if margin_pct is not None else "")
            + f", Risk: ${risk_amount_usd:.2f}, SL Dist: {sl_distance_pct*100:.2f}%, "
            f"Entry: {entry_price}, SL: {sl_price}, TP: {tp_price} (R:R {rr})"
        )
        
        return OrderParams(
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            quantity=quantity,
            position_size_usd=position_size_usd,
            leverage=leverage,
            direction=direction,
            risk_amount_usd=risk_amount_usd
        )
