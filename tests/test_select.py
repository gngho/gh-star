from datetime import datetime, timedelta, timezone

from agent import select as select_mod
from agent.models import RepoFacts

CFG = {
    "min_stars": 500,
    "stale_push_days": 90,
    "min_readme_chars": 500,
}


def _repo(**kwargs) -> RepoFacts:
    base = dict(
        full_name="o/r",
        owner="o",
        name="r",
        stars=1000,
        license_spdx="MIT",
        pushed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    base.update(kwargs)
    return RepoFacts(**base)


class TestCheapReject:
    """이 단계는 추가 API 호출 없이 Search 응답 필드만으로 판정해야 한다."""

    def test_accepts_healthy_repo(self):
        assert select_mod._cheap_reject(_repo(), CFG, set(), set()) is None

    def test_rejects_low_stars(self):
        assert select_mod._cheap_reject(_repo(stars=100), CFG, set(), set()) == "min_stars"

    def test_rejects_archived(self):
        assert select_mod._cheap_reject(_repo(archived=True), CFG, set(), set()) == "archived"

    def test_rejects_fork(self):
        assert select_mod._cheap_reject(_repo(fork=True), CFG, set(), set()) == "fork"

    def test_rejects_missing_license(self):
        assert (
            select_mod._cheap_reject(_repo(license_spdx=None), CFG, set(), set())
            == "no_license"
        )

    def test_rejects_recently_published(self):
        assert (
            select_mod._cheap_reject(_repo(), CFG, {"o/r"}, set()) == "recently_published"
        )

    def test_rejects_blocklisted(self):
        assert select_mod._cheap_reject(_repo(), CFG, set(), {"o/r"}) == "blocklisted"

    def test_rejects_stale_push(self):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        assert select_mod._cheap_reject(_repo(pushed_at=old), CFG, set(), set()) == "stale"

    def test_rejects_awesome_list_by_name(self):
        repo = _repo(name="awesome-llm", full_name="o/awesome-llm")
        assert select_mod._cheap_reject(repo, CFG, set(), set()) == "excluded_name"

    def test_rejects_tutorial_by_topic(self):
        repo = _repo(topics=["ai", "tutorial"])
        assert select_mod._cheap_reject(repo, CFG, set(), set()) == "excluded_topic"

    def test_rejects_roadmap_by_name(self):
        repo = _repo(name="ai-roadmap", full_name="o/ai-roadmap")
        assert select_mod._cheap_reject(repo, CFG, set(), set()) == "excluded_name"


class TestReadmeReject:
    def test_accepts_substantial_readme(self):
        assert select_mod._readme_reject("설명 " * 400, 500) is None

    def test_rejects_missing(self):
        assert select_mod._readme_reject(None, 500) == "no_readme"

    def test_rejects_short(self):
        assert select_mod._readme_reject("짧음", 500) == "readme_too_short"

    def test_rejects_link_list(self):
        body = "# Awesome\n\n" + "\n".join(
            f"- [Project {i}](https://example.com/{i})" for i in range(40)
        )
        assert select_mod._readme_reject(body, 10) == "link_list_readme"

    def test_prose_with_a_few_links_is_fine(self):
        lines = ["This project does a real thing and here is how it works."] * 40
        lines += ["- [docs](https://example.com)"] * 3
        assert select_mod._readme_reject("\n".join(lines), 10) is None
