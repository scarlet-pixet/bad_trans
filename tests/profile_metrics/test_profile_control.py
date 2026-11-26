from tools.profile_control import evaluate


def test_working_policy_matches_independent_control_oracles():
    results = evaluate()
    assert len(results["records"]) == 5
    assert {row["actualScore"] for row in results["records"]} == {0, 10, 40}
    assert len({row["policyHash"] for row in results["records"]}) == 1
