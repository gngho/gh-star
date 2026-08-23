"""CLI 진입점.

    python -m agent collect  [--date YYYY-MM-DD] [--dry-run]
    python -m agent select   [--date YYYY-MM-DD] [--dry-run]
    python -m agent research [--date YYYY-MM-DD] [--dry-run]
    python -m agent compose  [--date YYYY-MM-DD] [--dry-run]
    python -m agent render   [--date YYYY-MM-DD] [--dry-run]
    python -m agent design-sync [--dry-run]
    python -m agent status
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from . import collect as collect_mod
from . import paths
from . import select as select_mod
from .config import github_token, load_config

KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def _force_utf8_output() -> None:
    """Windows 콘솔 기본 코드페이지(cp949)에서 한글이 깨지는 것을 막는다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_collect(args: argparse.Namespace) -> int:
    config = load_config()
    result = collect_mod.run(config, args.date, dry_run=args.dry_run)

    print(f"\n[{result.date}] 후보 {len(result.candidates)}개")
    print(
        f"  소스: search={result.sources.get('search', 0)} "
        f"trending={result.sources.get('trending', 0)} "
        f"snapshot={result.sources.get('snapshot', 0)} "
        f"| 워치리스트 {result.watchlist_size}개"
    )
    for i, candidate in enumerate(result.candidates[:10], 1):
        s = candidate.score
        delta = f"{s.delta_1d:+d}" if s.delta_1d is not None else "?"
        print(
            f"  {i:2d}. {candidate.repo.full_name:<42} "
            f"score={s.total:.3f} conf={s.confidence:.2f} "
            f"Δ1d={delta:>7} ⭐{candidate.repo.stars:,}"
        )
    if not args.dry_run:
        print(f"\n  → {paths.candidates_path(result.date)}")
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    config = load_config()
    try:
        result = select_mod.run(config, args.date, dry_run=args.dry_run)
    except select_mod.NoCandidateError as exc:
        # 당일 스킵은 오류가 아니라 정상적인 결과다 (SPEC 11).
        print(f"\n[skip] {exc}")
        return 0
    except FileNotFoundError as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 1

    primary = result.primary
    assert primary is not None
    print(f"\n[{result.date}] 선정 완료 (검증 호출 {result.validation_calls}회)")
    print(f"  primary : {primary.full_name}")
    print(f"            {primary.reason}")
    if primary.flags:
        print(f"            ⚠ {', '.join(primary.flags)}")
    for backup in result.backups:
        print(f"  backup  : {backup.full_name}  ({backup.reason})")
    print(f"  탈락    : {len(result.rejected)}개")
    if not args.dry_run:
        print(f"\n  → {paths.selection_path(result.date)}")
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    from . import research as research_mod

    config = load_config()
    try:
        result = research_mod.run(config, args.date, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 1

    p = result.payload
    print(f"\n[{args.date}] 조사 완료: {result.repo}")
    print(f"  {p.one_liner}")
    print(f"  핵심 기능 {len(p.key_features)}개")
    for feature in p.key_features:
        print(f"    - {feature.title}  [근거: {', '.join(feature.evidence[:2])}]")
    if result.ungrounded_fields:
        print(f"  ⚠ 근거 없어 제외될 필드: {', '.join(result.ungrounded_fields)}")
    print(f"  비용 ${result.cost_usd or 0:.4f} / {result.num_turns}턴")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    from . import compose as compose_mod

    config = load_config()
    try:
        result = compose_mod.run(config, args.date, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 1

    print(f"\n[{result.date}] 원고 완료: {result.repo}")
    print(f"  카드 {len(result.payload.cards)}장 (시도 {result.attempts}회, ${result.cost_usd or 0:.4f})")
    for card in result.payload.cards:
        body = card.body.replace("\n", " ")
        print(f"  {card.index:2d}. [{card.role:12}] {card.title}")
        if body:
            print(f"      {body[:60]}{'…' if len(body) > 60 else ''}")
    if result.dropped_roles:
        print(f"  ⚠ 근거 부족으로 제외: {', '.join(result.dropped_roles)}")
    print(f"  해시태그 {len(result.payload.hashtags)}개")
    return 0


def cmd_illustrate(args: argparse.Namespace) -> int:
    from . import imagery

    try:
        deck, post_dir = imagery.load_deck(args.date)
        result = imagery.run(deck, post_dir, dry_run=args.dry_run)
    except (FileNotFoundError, imagery.ImageryError) as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 1

    print(f"\n[{args.date}] 이미지 준비: {result['repo']}")
    print(f"  OG {'있음' if result['og_available'] else '없음'} · "
          f"아바타 {'있음' if result['avatar_available'] else '없음'}")
    for item in result["images"]:
        print(f"  {item['index']:02d}.jpg  [{item['role']:12}] ← {item['source']}")
    if not args.dry_run:
        print(f"\n  → {result['dir']}")
        print("  카드를 다시 렌더하세요: python -m agent render")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from . import render as render_mod

    config = load_config()
    try:
        meta = render_mod.run(config, args.date, dry_run=args.dry_run)
    except (FileNotFoundError, render_mod.RenderError) as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 1

    print(f"\n[{meta['date']}] 렌더 완료: {meta['repo']}")
    print(f"  {meta['width']}×{meta['height']} {meta['format'].upper()} · 토큰 출처 {meta['tokens_source']}")
    for card in meta["cards"]:
        note = ""
        if card["overflow_px"]:
            note = f"  ⚠ {card['overflow_px']}px 초과"
        elif card["scale"] < 1.0:
            note = f"  ({card['scale']:.0%} 축소)"
        print(f"  {card['file']}  [{card['role']}]{note}")
    if meta["warnings"]:
        print(f"\n  ⚠ 경고 {len(meta['warnings'])}건 — 원고를 줄여야 합니다:")
        for w in meta["warnings"]:
            print(f"    - {w}")
    return 0


def cmd_design_sync(args: argparse.Namespace) -> int:
    from . import design_sync as ds

    try:
        result = ds.run(dry_run=args.dry_run)
    except ds.DesignSyncError as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 1

    state = "갱신됨" if result["changed"] else "변경 없음"
    print(f"\n디자인 동기화: 토큰 {result['tokens']}개 → {state}")
    print(f"  원본 : {result['source'] or '(미상)'}")
    print(f"  출력 : {result['css_path']}")
    if result["changed"] and not args.dry_run:
        print("\n  카드를 다시 렌더하세요: python -m agent render")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """로컬에서 한 번에 돌린다.

    수집·선정은 Actions 가 매일 알아서 하므로 기본값은 조사부터다.
    --full 을 주면 수집부터 전부 로컬에서 돌린다.
    """
    from . import compose as compose_mod
    from . import imagery
    from . import render as render_mod
    from . import research as research_mod

    config = load_config()
    date = args.date

    stages: list[tuple[str, Any]] = []
    if args.full:
        stages += [("수집", lambda: collect_mod.run(config, date)),
                   ("선정", lambda: select_mod.run(config, date))]
    stages += [
        ("조사", lambda: research_mod.run(config, date)),
        ("원고", lambda: compose_mod.run(config, date)),
    ]
    if not args.no_images:
        def _illustrate():
            deck, post_dir = imagery.load_deck(date)
            return imagery.run(deck, post_dir)
        stages.append(("이미지", _illustrate))
    stages.append(("렌더", lambda: render_mod.run(config, date)))

    for i, (label, fn) in enumerate(stages, 1):
        print(f"\n─── [{i}/{len(stages)}] {label} " + "─" * 40)
        try:
            fn()
        except select_mod.NoCandidateError as exc:
            print(f"\n[skip] {exc}")
            return 0
        except imagery.ImageryError as exc:
            # 이미지는 없어도 카드가 나온다. 여기서 멈출 이유가 없다.
            print(f"\n[warn] 이미지 생략: {exc}", file=sys.stderr)
            continue
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"\n[error] {label} 실패: {exc}", file=sys.stderr)
            return 1

    post_dirs = sorted(paths.POSTS.glob(f"{date}-*"))
    if not post_dirs:
        print("\n[error] 산출물 디렉터리를 찾지 못했습니다.", file=sys.stderr)
        return 1
    post_dir = post_dirs[-1]

    print("\n" + "═" * 56)
    print(f"  {post_dir}")

    meta = paths.read_json(post_dir / "meta.json", default={}) or {}
    cards = meta.get("cards", [])
    warnings = meta.get("warnings", [])
    print(f"  카드 {len(cards)}장 · 이미지 {sum(1 for c in cards if c.get('has_image'))}장")

    if warnings:
        # 넘치는 카드를 그대로 올리면 문장이 잘린 채 발행된다.
        print(f"\n  ⚠️ 렌더 경고 {len(warnings)}건 — 올리기 전에 확인하세요:")
        for w in warnings:
            print(f"     - {w}")

    caption = post_dir / "caption.md"
    if caption.exists():
        head = caption.read_text(encoding="utf-8").strip().split("\n")[0]
        print(f"\n  캡션: {caption.name}  \"{head[:52]}{'…' if len(head) > 52 else ''}\"")

    print("\n  올리는 법")
    print("   1. cards/ 를 01 부터 순서대로 인스타그램 캐러셀에 업로드")
    print("   2. caption.md 내용을 그대로 붙여넣기")
    print("   3. 올린 뒤 data/published.json 에 기록 (재발행 차단 기준)")
    print("\n  문구를 고치려면 content.json 수정 후 `python -m agent render`")

    if args.open:
        import subprocess
        subprocess.run(["explorer", str(post_dir / "cards")], check=False)
    return 0


def cmd_mark_published(args: argparse.Namespace) -> int:
    """올린 뒤 발행 이력에 기록한다. 이 기록이 90일 재발행 차단의 기준이다."""
    from .models import CardDeckFile

    matches = sorted(paths.POSTS.glob(f"{args.date}-*/content.json"))
    if not matches:
        print(f"\n[error] {args.date} 산출물이 없습니다.", file=sys.stderr)
        return 1
    deck = CardDeckFile.model_validate(paths.read_json(matches[0]))

    data = paths.read_json(paths.PUBLISHED, default={"posts": []}) or {"posts": []}
    posts = data.setdefault("posts", [])

    already = next(
        (p for p in posts if p.get("repo") == deck.repo and p.get("date") == args.date),
        None,
    )
    if already:
        if args.permalink:
            already["permalink"] = args.permalink
            paths.write_json(paths.PUBLISHED, data)
            print(f"\n기존 기록의 permalink 를 갱신했습니다: {deck.repo}")
            return 0
        print(f"\n이미 기록되어 있습니다: {deck.repo} ({args.date})")
        return 0

    posts.append(
        {
            "repo": deck.repo,
            "date": args.date,
            "card_count": len(deck.payload.cards),
            "permalink": args.permalink,
            "published_at": datetime.now(KST).isoformat(),
        }
    )
    paths.write_json(paths.PUBLISHED, data)

    print(f"\n발행 기록: {deck.repo} ({args.date}, 카드 {len(deck.payload.cards)}장)")
    print(f"  이 레포는 앞으로 90일간 다시 선정되지 않습니다.")
    print(f"  총 발행 {len(posts)}건")
    print("\n  커밋하는 것을 잊지 마세요: git add data/ posts/ && git commit && git push")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    snapshots = sorted(p.stem for p in paths.SNAPSHOTS.glob("*.json"))
    watchlist = paths.read_json(paths.WATCHLIST, default={}) or {}
    entries = watchlist.get("entries", {})

    print("파이프라인 상태")
    print(f"  GITHUB_TOKEN : {'설정됨' if github_token() else '없음 (축소 모드로 동작)'}")
    print(f"  스냅샷       : {len(snapshots)}일치", end="")
    if snapshots:
        print(f" ({snapshots[0]} ~ {snapshots[-1]})")
    else:
        print()
    print(f"  워치리스트   : {len(entries)}개")

    if len(snapshots) < 2:
        print("\n  Δ1d 는 스냅샷 2일, Δ7d 는 7일이 쌓여야 계산됩니다.")
        print("  그때까지는 트렌딩의 'stars today' 로 Δ1d 를 대신합니다.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("collect", cmd_collect, "핫 레포 후보를 수집하고 점수화한다"),
        ("select", cmd_select, "후보 중 심층 리뷰 대상 1개 + 예비 2개를 선정한다"),
        ("research", cmd_research, "선정된 레포를 에이전트가 심층 조사한다"),
        ("compose", cmd_compose, "조사 결과로 카드 원고를 생성한다"),
        ("illustrate", cmd_illustrate, "GitHub 자산으로 카드 상단 이미지를 만든다"),
        ("render", cmd_render, "원고를 카드 이미지(JPEG)로 렌더한다"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--date", default=_today_kst(), help="기준일 (KST, 기본: 오늘)")
        p.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않는다")
        p.set_defaults(func=handler)

    p_run = sub.add_parser(
        "run", help="조사→원고→이미지→렌더를 한 번에 돌린다 (로컬 실행용)"
    )
    p_run.add_argument("--date", default=_today_kst(), help="기준일 (KST, 기본: 오늘)")
    p_run.add_argument("--full", action="store_true", help="수집·선정도 로컬에서 함께 실행")
    p_run.add_argument("--no-images", action="store_true", help="상단 이미지 생략")
    p_run.add_argument("--open", action="store_true", help="끝나면 카드 폴더를 연다")
    p_run.set_defaults(func=cmd_run)

    p_sync = sub.add_parser(
        "design-sync", help="피그마 토큰으로 카드 CSS 를 다시 생성한다"
    )
    p_sync.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않는다")
    p_sync.set_defaults(func=cmd_design_sync)

    p_mark = sub.add_parser(
        "mark-published", help="인스타그램에 올린 뒤 발행 이력에 기록한다"
    )
    p_mark.add_argument("--date", default=_today_kst(), help="기준일 (KST, 기본: 오늘)")
    p_mark.add_argument("--permalink", default=None, help="게시물 URL (선택)")
    p_mark.set_defaults(func=cmd_mark_published)

    p_status = sub.add_parser("status", help="스냅샷 축적 현황을 확인한다")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    _force_utf8_output()
    _setup_logging(args.verbose)
    paths.ensure_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
