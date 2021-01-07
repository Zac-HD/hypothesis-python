from hypothesis import given, note, strategies as st


@st.composite
def things(draw):
    raise SystemExit


@given(st.data())
def test(data):
    x = data.draw(st.integers())
    note(x)
    if x > 100:
        data.draw(things())
        raise SystemExit
