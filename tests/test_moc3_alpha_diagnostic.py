# -*- coding: utf-8 -*-
"""Regression tests for MOC3 alpha diagnostic timing."""
from pathlib import Path
import unittest


class Moc3AlphaDiagnosticTests(unittest.TestCase):
    def test_diagnostic_waits_for_a_fully_loaded_model(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        start = runtime.index("reportAlphaDiagnostic(e){")
        end = runtime.index("createShader(){", start)
        diagnostic = runtime[start:end]

        self.assertIn("this._live2dManager?._models?.some(e=>e?._state===23&&e.getModel?.())", diagnostic)
        self.assertLess(diagnostic.index("if(!t)return"), diagnostic.index("e.readPixels("))
        self.assertIn("modelDrawn:!0", diagnostic)

    def test_diagnostic_reports_alpha_distribution_and_canvas_bounds(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        start = runtime.index("reportAlphaDiagnostic(e){")
        end = runtime.index("createShader(){", start)
        diagnostic = runtime[start:end]

        for field in (
            "transparentGridSamples",
            "semiTransparentGridSamples",
            "opaqueGridSamples",
            "whiteOpaqueGridSamples",
            "nonTransparentGridBounds",
            "samples",
        ):
            self.assertIn(field, diagnostic)
        self.assertIn("e.readPixels(", diagnostic)
        self.assertIn("window.parent.postMessage(p,window.location.origin)", diagnostic)

    def test_diagnostic_exception_does_not_permanently_disable_retries(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn('try{this.reportAlphaDiagnostic(e)}catch(t){console.warn("[MOC3 alpha diagnostic] failed; will retry next frame",t)}', runtime)
        self.assertNotIn('catch(t){this._alphaDiagnosticSent=!0,console.warn("[MOC3 alpha diagnostic] failed"', runtime)


if __name__ == "__main__":
    unittest.main()
