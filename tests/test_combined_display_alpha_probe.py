from pathlib import Path


def test_combined_display_alpha_probe_is_green_and_embeds_real_output():
    root = Path(__file__).resolve().parents[1]
    app = (root / "main.py").read_text(encoding="utf-8")
    template = (root / "templates" / "combined_display_alpha_probe.html").read_text(encoding="utf-8")

    assert '/display/combined-alpha-probe/<layout_id>' in app
    assert 'combined_display_alpha_probe.html' in app
    assert 'background: #00ff00 !important' in template
    assert 'allowtransparency="true"' in template
    assert 'src="{{ combined_url }}"' in template
