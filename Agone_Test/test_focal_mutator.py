import os
import tempfile
import unittest

import javalang

try:
    from Agone_Test import focal_mutator
except ModuleNotFoundError:
    import focal_mutator


class FocalMutatorTests(unittest.TestCase):
    def _write_java_source(self, source_text):
        temp_dir = tempfile.TemporaryDirectory()
        file_path = os.path.join(temp_dir.name, "Sample.java")
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(source_text)
        return temp_dir, file_path

    def test_exception_mutation_injects_assertion_error_guard(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public int target(int x) {\n"
            "        return x + 1;\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        result = focal_mutator.apply_exception_mutation(file_path, target_method="target")

        with open(file_path, "r", encoding="utf-8") as handle:
            mutated = handle.read()

        self.assertIn("System.getProperty(\"agone.mutation.trigger\") == null", mutated)
        self.assertIn("throw new AssertionError(\"AGONE_MUTATION_TRIGGER\")", mutated)
        self.assertNotIn("throws IllegalArgumentException", mutated)
        self.assertEqual(result["mutation_type"], "exception")
        self.assertEqual(result["method_name"], "target")
        self.assertEqual(result["added_exception"], "AssertionError(\"AGONE_MUTATION_TRIGGER\")")

        # Ensure the resulting source is still parseable Java.
        javalang.parse.parse(mutated)

    def test_exception_mutation_rejects_when_trigger_already_present(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public void target() {\n"
            "        if (System.getProperty(\"agone.mutation.trigger\") == null) "
            "{ throw new AssertionError(\"AGONE_MUTATION_TRIGGER\"); }\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaises(ValueError):
            focal_mutator.apply_exception_mutation(file_path, target_method="target")


if __name__ == "__main__":
    unittest.main()
