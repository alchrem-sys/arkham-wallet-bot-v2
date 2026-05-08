from config import CHAIN_NAMES, CHAIN_EXPLORERS, CHAIN_ADDR_EXPLORERS, CHAIN_NATIVE

ARKHAM_ADDR = "https://intel.arkm.com/explorer/address/{}"


def short_addr(addr: str) -> str:
    addr = addr.lower()
    return f"{addr[:6]}...{addr[-4:]}"


def fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.2f}K"
    return f"${val:,.2f}"


def fmt_amount(val: float) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"{val / 1_000:.2f}K"
    if val < 0.0001:
        return f"{val:.8f}"
    if val < 1:
        return f"{val:.4f}"
    return f"{val:,.4f}"


def percentage_flag(pct: float) -> str:
    if pct >= 80:
        return "🚨🔴"
    if pct >= 40:
        return "⚠️🟡"
    if pct >= 10:
        return "🟢"
    return ""


def _arkham_link(address: str, display: str) -> str:
    url = ARKHAM_ADDR.format(address.lower())
    return f'<a href="{url}">{display}</a>'


def addr_link(chain_id: str, address: str, label: str | None = None) -> str:
    url = ARKHAM_ADDR.format(address.lower())
    display = label if label else short_addr(address)
    return f'<a href="{url}">{display}</a>'


def _wallet_title(address: str, name: str | None) -> str:
    display = name or short_addr(address)
    return _arkham_link(address, f"<b>{display}</b>")


def format_portfolio(
    address: str,
    total_usd: float,
    name: str | None = None,
    threshold: float | None = None,
    entity: str | None = None,
    chain_breakdown: dict[str, float] | None = None,
) -> str:
    title = _wallet_title(address, name)
    lines = [
        f"📊 {title}",
        f"<code>{address.lower()}</code>",
    ]
    if entity and entity != name:
        lines.append(f"🏷 <i>{entity}</i>")

    lines += [
        "",
        f"💰 <b>Total: {fmt_usd(total_usd)}</b>",
    ]

    if chain_breakdown:
        sorted_chains = sorted(chain_breakdown.items(), key=lambda x: x[1], reverse=True)
        active = [(c, v) for c, v in sorted_chains if v >= 1]
        if active:
            lines.append("")
            lines.append("<b>By chain:</b>")
            for chain_name, chain_val in active:
                display = CHAIN_NAMES.get(chain_name, chain_name.capitalize())
                lines.append(f"  {display}: <b>{fmt_usd(chain_val)}</b>")

    lines += [
        "",
        f"✅ Monitoring started",
    ]
    if threshold is not None:
        lines.append(f"📏 Min alert: <b>{fmt_usd(threshold)}</b>")
    else:
        lines.append(f"📏 No threshold (all transfers)")

    return "\n".join(lines)


def format_batch(
    events: list[dict],
    watched_address: str,
    name: str | None,
    total_usd: float,
    labels: dict[str, str] | None = None,
) -> str:
    groups: dict[str, list[dict]] = {}
    for ev in events:
        groups.setdefault(ev["hash"], []).append(ev)

    if len(groups) == 1:
        return format_tx_group(events, watched_address, name, total_usd, labels)

    watched = watched_address.lower()
    chain_id = events[0].get("chain_id", "0x1")
    title = _wallet_title(watched_address, name)

    total_value = sum(float(e.get("value_usd") or 0) for e in events)
    pct = (total_value / total_usd * 100) if total_usd > 0 and total_value > 0 else 0
    flag = percentage_flag(pct) if pct > 0 else ""

    header_parts = ["📦", title, f"· <i>{len(groups)} transactions</i>"]
    if flag:
        header_parts.insert(0, flag)
    lines = [" ".join(header_parts), ""]

    for tx_hash, group in groups.items():
        outs = [e for e in group if e["from"] == watched]
        ins = [e for e in group if e["to"] == watched]
        tx_url = CHAIN_EXPLORERS.get(chain_id, "https://etherscan.io/tx/{}").format(tx_hash)

        if outs and ins:
            arrow = "🔄"
            body = f"{' + '.join(_token_line(e) for e in outs)} → {' + '.join(_token_line(e) for e in ins)}"
        elif outs:
            arrow = "📤"
            other = outs[0]["to"]
            lbl = (labels or {}).get(other)
            body = f"{_token_line(outs[0])} → {addr_link(chain_id, other, lbl)}"
        elif ins:
            arrow = "📥"
            other = ins[0]["from"]
            lbl = (labels or {}).get(other)
            body = f"{addr_link(chain_id, other, lbl)} → {_token_line(ins[0])}"
        else:
            arrow = "•"
            body = ""

        lines.append(f"{arrow} {body}  <a href=\"{tx_url}\">tx</a>")

    if total_value > 0:
        lines.append("")
        lines.append(f"💵 Total: <b>{fmt_usd(total_value)}</b>")
    if pct > 0:
        lines.append(f"📊 {pct:.1f}% of {fmt_usd(total_usd)}")

    return "\n".join(lines)


def format_tx_group(
    events: list[dict],
    watched_address: str,
    name: str | None,
    total_usd: float,
    labels: dict[str, str] | None = None,
) -> str:
    if not events:
        return ""

    watched = watched_address.lower()
    chain_id = events[0].get("chain_id", "0x1")
    tx_hash = events[0].get("hash", "")
    tx_url = CHAIN_EXPLORERS.get(chain_id, "https://etherscan.io/tx/{}").format(tx_hash)

    outs = [e for e in events if e["from"] == watched]
    ins = [e for e in events if e["to"] == watched]

    counterparties: list[str] = []
    for e in outs:
        if e["to"] and e["to"] not in counterparties:
            counterparties.append(e["to"])
    for e in ins:
        if e["from"] and e["from"] not in counterparties:
            counterparties.append(e["from"])

    total_value = sum(float(e.get("value_usd") or 0) for e in events)
    pct = (total_value / total_usd * 100) if total_usd > 0 and total_value > 0 else 0
    flag = percentage_flag(pct) if pct > 0 else ""

    title = _wallet_title(watched_address, name)

    if outs and ins:
        action_emoji = "🔄"
        action_word = "SWAP"
    elif outs:
        action_emoji = "📤"
        action_word = "OUT"
    elif ins:
        action_emoji = "📥"
        action_word = "IN"
    else:
        action_emoji = "•"
        action_word = ""

    header_parts = [action_emoji, title, f"· <i>{action_word}</i>"]
    if flag:
        header_parts.insert(0, flag)
    lines = [" ".join(header_parts)]

    if outs and ins:
        out_strs = [_token_line(e) for e in outs]
        in_strs = [_token_line(e) for e in ins]
        lines.append(f"{' + '.join(out_strs)} → {' + '.join(in_strs)}")
    else:
        for e in events:
            lines.append(_token_line(e))

    if total_value > 0:
        lines.append(f"💵 <b>{fmt_usd(total_value)}</b>")
    if pct > 0:
        lines.append(f"📊 {pct:.1f}% of {fmt_usd(total_usd)}")

    if counterparties:
        lines.append("")
        lbl_map = labels or {}
        if len(counterparties) == 1:
            other = counterparties[0]
            lbl = lbl_map.get(other)
            link = addr_link(chain_id, other, lbl)
            if outs and not ins:
                lines.append(f"→ {link}")
            elif ins and not outs:
                lines.append(f"← {link}")
            else:
                lines.append(f"↔ {link}")
        else:
            parts = []
            for c in counterparties[:3]:
                lbl = lbl_map.get(c)
                parts.append(addr_link(chain_id, c, lbl))
            lines.append("With: " + ", ".join(parts))

    if tx_hash:
        lines.append(f'<a href="{tx_url}">Tx hash</a>')

    return "\n".join(lines)


def _token_line(ev: dict) -> str:
    token = ev.get("token_symbol", "?")
    amount = ev.get("amount", "")
    value_usd = float(ev.get("value_usd") or 0)
    if value_usd > 0:
        return f"{amount} {token} (${value_usd:,.2f})"
    return f"{amount} {token}"


def format_transfer(
    tx: dict,
    watched_address: str,
    name: str | None,
    total_usd: float,
) -> str:
    chain_id = tx.get("chain_id", "0x1")
    tx_hash = tx.get("hash", "")
    from_addr = (tx.get("from") or "").lower()
    to_addr = (tx.get("to") or "").lower()
    watched = watched_address.lower()

    direction = "OUT" if from_addr == watched else "IN"
    arrow_emoji = "📤" if direction == "OUT" else "📥"

    token = tx.get("token_symbol") or CHAIN_NATIVE.get(chain_id, "ETH")
    amount = tx.get("amount", "")
    value_usd = float(tx.get("value_usd") or 0)

    tx_url = CHAIN_EXPLORERS.get(chain_id, "https://etherscan.io/tx/{}").format(tx_hash)

    pct = 0.0
    flag = ""
    if total_usd > 0 and value_usd > 0:
        pct = (value_usd / total_usd) * 100
        flag = percentage_flag(pct)

    title = _wallet_title(watched_address, name)
    header_parts = [arrow_emoji, title]
    if flag:
        header_parts.insert(0, flag)

    lines = [" ".join(header_parts)]

    if value_usd > 0:
        lines.append(f"<b>{fmt_usd(value_usd)}</b> ({amount} {token})")
    else:
        lines.append(f"<b>{amount} {token}</b>")

    if pct > 0:
        lines.append(f"📊 {pct:.1f}% of {fmt_usd(total_usd)}")

    lines.append("")
    lines.append(f"{addr_link(chain_id, from_addr)} → {addr_link(chain_id, to_addr)}")

    if tx_hash:
        lines.append(f'<a href="{tx_url}">Tx hash</a>')

    return "\n".join(lines)
