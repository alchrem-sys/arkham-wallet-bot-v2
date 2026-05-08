from config import CHAIN_NAMES, CHAIN_EXPLORERS

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
    return f"{val:,.2f}"


def arkham_link(address: str, label: str | None = None) -> str:
    url = ARKHAM_ADDR.format(address.lower())
    display = label if label else short_addr(address)
    return f'<a href="{url}">{display}</a>'


def tx_link(chain_id: str, tx_hash: str) -> str:
    url = CHAIN_EXPLORERS.get(chain_id, "https://etherscan.io/tx/{}").format(tx_hash)
    return f'<a href="{url}">tx</a>'


def format_analysis(result) -> list[str]:
    from analyzer import AnalysisResult, FlaggedTransfer
    r: AnalysisResult = result

    title = arkham_link(r.address, r.entity)
    messages = []

    # Header message
    lines = [
        f"🔍 <b>Wallet Analysis</b>",
        f"{title}",
        f"<code>{r.address.lower()}</code>",
        "",
        f"💰 Balance: <b>{fmt_usd(r.total_usd)}</b>",
        f"📊 Transfers (7d): <b>{r.total_transfers}</b>",
        f"⚠️ Flagged: <b>{len(r.flagged)}</b>",
    ]

    if r.summary:
        lines.append("")
        lines.append("<b>Summary:</b>")
        for flag, count in sorted(r.summary.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {flag}: <b>{count}</b>")

    if not r.flagged:
        lines.append("")
        lines.append("✅ No suspicious transfers found")
        messages.append("\n".join(lines))
        return messages

    messages.append("\n".join(lines))

    # Group flagged transfers into messages (Telegram 4096 char limit)
    watched = r.address.lower()
    chunk_lines = []

    for i, ft in enumerate(r.flagged[:50]):
        is_out = ft.from_addr == watched
        arrow = "📤" if is_out else "📥"

        if is_out:
            counterparty = arkham_link(ft.to_addr, ft.to_label)
            direction = f"→ {counterparty}"
        else:
            counterparty = arkham_link(ft.from_addr, ft.from_label)
            direction = f"← {counterparty}"

        amount_str = fmt_amount(ft.amount) if ft.amount else ""
        usd_str = f" ({fmt_usd(ft.usd)})" if ft.usd > 0 else ""

        tx = tx_link(ft.chain_id, ft.tx_hash)
        chain_name = CHAIN_NAMES.get(ft.chain_id, ft.chain)

        ts = ""
        if ft.timestamp:
            ts = ft.timestamp[:10]

        flag_str = " · ".join(ft.flags)

        entry = [
            f"{arrow} <b>{amount_str} {ft.token}</b>{usd_str}",
            f"  {direction}  {tx}  <i>{chain_name}</i>",
            f"  {flag_str}",
        ]
        if ts:
            entry[0] = f"{ts}  {entry[0]}"

        candidate = "\n".join(entry)

        if sum(len(l) for l in chunk_lines) + len(candidate) + 10 > 3800:
            messages.append("\n\n".join(chunk_lines))
            chunk_lines = []

        chunk_lines.append(candidate)

    if chunk_lines:
        messages.append("\n\n".join(chunk_lines))

    return messages
