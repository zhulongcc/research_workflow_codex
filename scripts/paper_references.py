"""Human-readable paper/code references shared by annotation and validation."""
from __future__ import annotations


def mapping_key(item: dict) -> tuple:
    return (item.get('paper_location'), item.get('code_file'), item.get('symbol'),
            tuple(item.get('code_lines') or []))


def citation(title: str, item: dict) -> str:
    start, end = item['code_lines']
    return f"论文对应：{title}｜{item['paper_location']}｜{item['code_file']}:{start}-{end}::{item['symbol']}"
