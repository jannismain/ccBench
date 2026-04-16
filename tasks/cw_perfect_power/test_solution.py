from project.solution import is_perfect_power as pp


def test_small_examples():
    assert pp(4) == [2, 2], "4 = 2^2"
    assert pp(9) == [3, 2], "9 = 3^2"
    assert pp(5) is None, "5 isn't a perfect power"


def test_small_perfect_powers():
    pp = [
        4,
        8,
        9,
        16,
        25,
        27,
        32,
        36,
        49,
        64,
        81,
        100,
        121,
        125,
        128,
        144,
        169,
        196,
        216,
        225,
        243,
        256,
        289,
        324,
        343,
        361,
        400,
        441,
        484,
    ]
    for item in pp:
        actual = pp(item)
        if actual is None:
            assert pp(item) is not None, f"The perfect power {item} wasn't recognized as one"
        else:
            assert actual[0] ** actual[1] == item, (
                f"Your pair {actual} doesn't work for {item}"
            )


def test_bigger_perfect_powers():
    pp = [1089, 1156, 1225, 1296, 1331, 1369, 1444, 1521, 1600, 1681, 1728, 1764]
    for item in pp:
        actual = pp(item)
        if actual is None:
            assert pp(item) is not None, f"The perfect power {item} wasn't recognized as one"
        else:
            assert actual[0] ** actual[1] == item, (
                f"Your pair {actual} doesn't work for {item}"
            )


def test_all_perfect_powers_up_to_limit():
    for m in range(2, 100):
        for k in range(2, 100):
            n = m**k
            actual = pp(n)
            if actual is None:
                assert pp(n) is not None, f"The perfect power {n} wasn't recognized as one"
            else:
                assert actual[0] ** actual[1] == n, f"Your pair {actual} doesn't work for {n}"
