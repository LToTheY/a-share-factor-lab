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


def test_public_case_links_to_rendered_synthetic_image_inside_tutorial():
    case = AppTest.from_string("from dashboard.views.methodology import render\nrender()")
    case.query_params["guide"] = "CASE_STUDY.md"
    case.run()
    assert not case.exception
    assert any("?guide=../examples/synthetic_demo/README.md" in block.value for block in case.markdown)
    example = AppTest.from_string("from dashboard.views.methodology import render\nrender()")
    example.query_params["guide"] = "../examples/synthetic_demo/README.md"
    example.run()
    assert not example.exception
    assert example.selectbox[0].value == "合成演示图表"
    assert sum(element.type in {"image", "imgs"} for element in example) == 1
    assert any("不代表真实投资业绩" in block.value for block in example.markdown)


def test_project_readme_is_readable_and_its_docs_links_stay_inside_tutorial():
    page = AppTest.from_string("from dashboard.views.methodology import render\nrender()")
    page.query_params["guide"] = "../README.md"
    page.run()
    assert not page.exception
    assert page.selectbox[0].value == "项目首页说明"
    assert any("?guide=USER_GUIDE.md" in block.value for block in page.markdown)
