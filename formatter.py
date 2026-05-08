from config import CHAIN_NAMES, CHAIN_EXPLORERS

ARKHAM_ADDR = "https://intel.arkm.com/explorer/address/{}"


def short_addr(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}"


def fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:,.0f}"


def fmt_amount(val: float) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"{val / 1_000:.1f}K"
    if val < 0.01:
        return f"{val:.6f}"
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
    from analyzer import AnalysisResult
    r: AnalysisResult = result

    title = arkham_link(r.address, r.entity)
    messages = []

    lines = [
        f"🔍 <b>Wallet Analysis</b> (7d, $10K+)",
        f"{title}",
        f"<code>{r.address.lower()}</code>",
        "",
        f"💰 Balance: <b>{fmt_usd(r.total_usd)}</b>",
        f"📊 Total txs: {r.total_transfers} · Scanned ($10K+): {r.scanned_transfers}",
        f"⚠️ Flagged: <b>{len(r.flagged)}</b>",
    ]

    # Token flow summary
    if r.token_flows:
        lines.append("")
        lines.append("<b>Token flows ($10K+):</b>")
        sorted_tokens = sorted(
            r.token_flows.items(),
            key=lambda x: x[1]["out_usd"] + x[1]["in_usd"],
            reverse=True,
        )
        for token, flow in sorted_tokens[:10]:
            parts = []
            if flow["out_usd"] > 0:
                parts.append(f"📤 {fmt_usd(flow['out_usd'])} ({flow['out_count']})")
            if flow["in_usd"] > 0:
                parts.append(f"📥 {fmt_usd(flow['in_usd'])} ({flow['in_count']})")
            lines.append(f"  <b>{token}</b>: {' · '.join(parts)}")

    if r.summary:
        lines.append("")
        lines.append("<b>Flags:</b>")
        for flag, count in sorted(r.summary.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {flag}: <b>{count}</b>")

    if not r.flagged:
        lines.append("")
        lines.append("✅ No suspicious transfers found")
        messages.append("\n".join(lines))
        return messages

    messages.append("\n".join(lines))

    # Flagged transfers grouped by messages
    watched = r.address.lower()
    chunk_lines = []

    for ft in r.flagged[:40]:
        is_out = ft.from_addr == watched
        arrow = "📤" if is_out else "📥"

        if is_out:
            counterparty = arkham_link(ft.to_addr, ft.to_label)
            direction = f"→ {counterparty}"
        else:
            counterparty = arkham_link(ft.from_addr, ft.from_label)
            direction = f"← {counterparty}"

        amount_str = fmt_amount(ft.amount) if ft.amount else ""
        chain_name = CHAIN_NAMES.get(ft.chain_id, ft.chain)
        tx = tx_link(ft.chain_id, ft.tx_hash)
        ts = ft.timestamp[:10] if ft.timestamp else ""
        flag_str = " · ".join(ft.flags)

        entry = (
            f"{ts}  {arrow} <b>{amount_str} {ft.token}</b> ({fmt_usd(ft.usd)})\n"
            f"  {direction}  {tx}  <i>{chain_name}</i>\n"
            f"  {flag_str}"
        )

        if sum(len(l) for l in chunk_lines) + len(entry) + 10 > 3800:
            messages.append("\n\n".join(chunk_lines))
            chunk_lines = []

        chunk_lines.append(entry)

    if chunk_lines:
        messages.append("\n\n".join(chunk_lines))

    return messages
