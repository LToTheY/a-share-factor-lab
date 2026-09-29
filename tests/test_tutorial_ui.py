from streamlit.testing.v1 import AppTest


def test_manual_links_and_deep_linked_course():
    app = AppTest.from_string("from dashboard.views.methodology import render\nrender()")
    app.run()
    assert not app.exception
    assert any("?guide=BEGINNER_COURSE.md" in block.value for block in app.markdown)
    lesson = AppTest.from_string("from dashboard.views.methodology import render\nrender()")
    lesson.query_params["guide"] = "BEGINNER_COURSE.md"
    lesson.run()
    assert not lesson.exception
    assert lesson.selectbox[0].value == "小白学习路线"
    assert any("20日" in block.value for block in lesson.markdown)
