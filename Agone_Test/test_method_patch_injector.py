import unittest

try:
    from Agone_Test import utils
except ModuleNotFoundError:
    import utils


ORIGINAL_TEST_CLASS = """package demo;

import org.junit.Test;

public class SampleTest {

    @Test
    public void targetTest() {
        int value = 1;
        org.junit.Assert.assertEquals(1, value);
    }

    @Test
    public void untouchedTest() {
        org.junit.Assert.assertTrue(true);
    }

    private String helper() {
        return "ok";
    }
}
"""


class MethodPatchInjectorTests(unittest.TestCase):
    def test_injector_replaces_only_target_test_method(self):
        method_patch = """@Test
    public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }"""

        injected_ok, patched_source, patch_error = utils.inject_mapped_test_method_patch(
            ORIGINAL_TEST_CLASS,
            method_patch,
            "targetTest",
        )
        self.assertTrue(injected_ok, msg=patch_error)
        self.assertIsNotNone(patched_source)

        boundary_ok, boundary_reason = utils._validate_strict_test_repair_boundaries(
            ORIGINAL_TEST_CLASS,
            patched_source,
            technique="iterative-healing",
            target_test_method="targetTest",
        )
        self.assertTrue(boundary_ok, msg=boundary_reason)

        original_blocks = utils._extract_method_blocks_from_source(
            ORIGINAL_TEST_CLASS, test_methods_only=False
        )
        patched_blocks = utils._extract_method_blocks_from_source(
            patched_source, test_methods_only=False
        )
        self.assertEqual(
            original_blocks["untouchedTest"].strip(),
            patched_blocks["untouchedTest"].strip(),
        )
        self.assertEqual(
            original_blocks["helper"].strip(),
            patched_blocks["helper"].strip(),
        )

    def test_injector_rejects_malformed_patch(self):
        injected_ok, patched_source, patch_error = utils.inject_mapped_test_method_patch(
            ORIGINAL_TEST_CLASS,
            "this is not a java method",
            "targetTest",
        )
        self.assertFalse(injected_ok)
        self.assertIsNone(patched_source)
        self.assertIn("targetTest", patch_error)

    def test_iterative_boundary_rejects_unmapped_changes(self):
        candidate = """package demo;

import org.junit.Test;

public class SampleTest {

    @Test
    public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }

    @Test
    public void untouchedTest() {
        org.junit.Assert.assertTrue(false);
    }

    private String helper() {
        return "changed";
    }
}
"""
        boundary_ok, boundary_reason = utils._validate_strict_test_repair_boundaries(
            ORIGINAL_TEST_CLASS,
            candidate,
            technique="iterative-healing",
            target_test_method="targetTest",
        )
        self.assertFalse(boundary_ok)
        self.assertTrue(
            "unmapped @Test method changed" in boundary_reason
            or "non-target method changed" in boundary_reason
        )

    def test_regenerative_boundary_allows_added_test(self):
        candidate = """package demo;

import org.junit.Test;

public class SampleTest {

    @Test
    public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }

    @Test
    public void addedCoverageTest() {
        org.junit.Assert.assertNotNull(new Object());
    }

    @Test
    public void untouchedTest() {
        org.junit.Assert.assertTrue(true);
    }

    private String helper() {
        return "ok";
    }
}
"""
        boundary_ok, boundary_reason = utils._validate_strict_test_repair_boundaries(
            ORIGINAL_TEST_CLASS,
            candidate,
            technique="regenerative-sync",
            target_test_method="targetTest",
        )
        self.assertTrue(boundary_ok, msg=boundary_reason)

    def test_style_lock_accepts_signature_preserving_patch(self):
        candidate_patch = """@Test
    public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertTrue(style_ok, msg=style_reason)

    def test_style_lock_accepts_throws_change(self):
        candidate_patch = """@Test
    public void targetTest() throws Exception {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertTrue(style_ok, msg=style_reason)

    def test_style_lock_rejects_modifier_change(self):
        candidate_patch = """@Test
    private void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertFalse(style_ok)
        self.assertIn("declaration lock changed", style_reason)

    def test_style_lock_rejects_annotation_change(self):
        candidate_patch = """public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertFalse(style_ok)
        self.assertIn("declaration lock changed", style_reason)

    def test_style_lock_rejects_return_type_change(self):
        candidate_patch = """@Test
    public int targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
        return value;
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertFalse(style_ok)
        self.assertIn("declaration lock changed", style_reason)

    def test_style_lock_rejects_local_mock_creation(self):
        candidate_patch = """@Test
    public void targetTest() {
        Object dep = Mockito.mock(Object.class);
        org.junit.Assert.assertNotNull(dep);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertFalse(style_ok)
        self.assertIn("local Mockito mock creation", style_reason)

    def test_style_lock_rejects_local_focal_instantiation(self):
        original_with_focal_ref = """package demo;

import org.junit.Test;

public class SampleTest {

    private SampleService sampleService;

    @Test
    public void targetTest() {
        org.junit.Assert.assertNotNull(sampleService);
    }
}
"""
        candidate_patch = """@Test
    public void targetTest() {
        SampleService sampleService = new SampleService();
        org.junit.Assert.assertNotNull(sampleService);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            original_with_focal_ref,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertFalse(style_ok)
        self.assertIn("local focal-class instantiation", style_reason)

    def test_style_lock_rejects_new_helper_call_not_in_original_class(self):
        candidate_patch = """@Test
    public void targetTest() {
        injectFieldByType(new Object(), Object.class, null);
        org.junit.Assert.assertTrue(true);
    }"""
        style_ok, style_reason = utils._validate_iterative_method_style_lock(
            ORIGINAL_TEST_CLASS,
            candidate_patch,
            "targetTest",
            "SampleService",
        )
        self.assertFalse(style_ok)
        self.assertIn("injectFieldByType", style_reason)

    def test_regenerative_additive_injector_replaces_target_and_appends_new_test(self):
        method_patch_bundle = """@Test
    public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }

    @Test
    public void addedCoverageTest() {
        org.junit.Assert.assertNotNull(new Object());
    }"""

        injected_ok, patched_source, patch_error = utils.inject_regenerative_test_method_patch_bundle(
            ORIGINAL_TEST_CLASS,
            method_patch_bundle,
            "targetTest",
        )
        self.assertTrue(injected_ok, msg=patch_error)
        self.assertIsNotNone(patched_source)
        self.assertIn("addedCoverageTest", patched_source)

        boundary_ok, boundary_reason = utils._validate_strict_test_repair_boundaries(
            ORIGINAL_TEST_CLASS,
            patched_source,
            technique="regenerative-sync",
            target_test_method="targetTest",
        )
        self.assertTrue(boundary_ok, msg=boundary_reason)

    def test_regenerative_additive_injector_rejects_unmapped_redefinition(self):
        method_patch_bundle = """@Test
    public void targetTest() {
        int value = 2;
        org.junit.Assert.assertEquals(2, value);
    }

    @Test
    public void untouchedTest() {
        org.junit.Assert.assertTrue(false);
    }"""

        injected_ok, patched_source, patch_error = utils.inject_regenerative_test_method_patch_bundle(
            ORIGINAL_TEST_CLASS,
            method_patch_bundle,
            "targetTest",
        )
        self.assertFalse(injected_ok)
        self.assertIsNone(patched_source)
        self.assertIn("redefine existing unmapped @Test method", patch_error)

    def test_regenerative_additive_injector_rejects_missing_target_method(self):
        method_patch_bundle = """@Test
    public void addedCoverageTest() {
        org.junit.Assert.assertNotNull(new Object());
    }"""

        injected_ok, patched_source, patch_error = utils.inject_regenerative_test_method_patch_bundle(
            ORIGINAL_TEST_CLASS,
            method_patch_bundle,
            "targetTest",
        )
        self.assertFalse(injected_ok)
        self.assertIsNone(patched_source)
        self.assertIn("targetTest", patch_error)


if __name__ == "__main__":
    unittest.main()
