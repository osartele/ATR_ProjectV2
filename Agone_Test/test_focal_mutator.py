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

    def test_exception_mutation_targets_overload_by_parameter_count(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public int target(int x) {\n"
            "        return x + 1;\n"
            "    }\n"
            "    public int target(int x, int y) {\n"
            "        return x + y;\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        result = focal_mutator.apply_exception_mutation(
            file_path,
            target_method="target",
            target_parameter_count=2,
        )

        with open(file_path, "r", encoding="utf-8") as handle:
            mutated = handle.read()

        first_overload_start = mutated.index("public int target(int x)")
        second_overload_start = mutated.index("public int target(int x, int y)")
        mutation_marker_index = mutated.index("AGONE_MUTATION_TRIGGER")

        self.assertGreater(mutation_marker_index, second_overload_start)
        self.assertNotIn("AGONE_MUTATION_TRIGGER", mutated[first_overload_start:second_overload_start])
        self.assertEqual(result["method_name"], "target")

    def test_exception_mutation_prefers_catch_block_scope(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public int target(String value) {\n"
            "        try {\n"
            "            return Integer.parseInt(value);\n"
            "        } catch (NumberFormatException ex) {\n"
            "            return -1;\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        result = focal_mutator.apply_exception_mutation(
            file_path,
            target_method="target",
            target_parameter_count=1,
        )

        with open(file_path, "r", encoding="utf-8") as handle:
            mutated = handle.read()

        catch_start = mutated.index("} catch (NumberFormatException ex) {")
        trigger_index = mutated.index("AGONE_MUTATION_TRIGGER")
        self.assertGreater(trigger_index, catch_start)
        self.assertEqual(result["injection_scope"], "catch_block")

    def test_exception_mutation_allows_forcing_method_entry_scope(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public int target(String value) {\n"
            "        try {\n"
            "            return Integer.parseInt(value);\n"
            "        } catch (NumberFormatException ex) {\n"
            "            return -1;\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        result = focal_mutator.apply_exception_mutation(
            file_path,
            target_method="target",
            target_parameter_count=1,
            preferred_injection_scope="method_entry",
        )

        with open(file_path, "r", encoding="utf-8") as handle:
            mutated = handle.read()

        method_start = mutated.index("public int target(String value) {")
        catch_start = mutated.index("} catch (NumberFormatException ex) {")
        trigger_index = mutated.index("AGONE_MUTATION_TRIGGER")
        self.assertGreater(trigger_index, method_start)
        self.assertLess(trigger_index, catch_start)
        self.assertEqual(result["injection_scope"], "method_entry")

    def test_exception_mutation_rejects_forced_catch_scope_when_missing(self):
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

        with self.assertRaises(ValueError):
            focal_mutator.apply_exception_mutation(
                file_path,
                target_method="target",
                target_parameter_count=1,
                preferred_injection_scope="catch_block",
            )

    def test_logical_mutation_skips_null_guard_comparisons(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public int target(Object value) {\n"
            "        if (value == null) {\n"
            "            throw new IllegalStateException(\"value is required\");\n"
            "        }\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        with self.assertRaises(ValueError):
            focal_mutator.apply_logical_mutation(
                file_path,
                target_method="target",
                target_parameter_count=1,
            )

    def test_logical_mutation_can_select_candidate_index(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public boolean target(int x, int y) {\n"
            "        return x > 0 && y < 10;\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        result = focal_mutator.apply_logical_mutation(
            file_path,
            target_method="target",
            target_parameter_count=2,
            preferred_candidate_index=1,
        )

        with open(file_path, "r", encoding="utf-8") as handle:
            mutated = handle.read()

        self.assertIn("x > 0 && y >= 10", mutated)
        self.assertEqual(result["mutation_candidate_index"], 1)
        self.assertEqual(result["old_operator"], "<")
        self.assertEqual(result["new_operator"], ">=")

    def test_logical_mutation_supports_unary_not_removal(self):
        source_text = (
            "package demo;\n"
            "public class Sample {\n"
            "    public boolean target(boolean ready) {\n"
            "        return !ready;\n"
            "    }\n"
            "}\n"
        )
        temp_dir, file_path = self._write_java_source(source_text)
        self.addCleanup(temp_dir.cleanup)

        result = focal_mutator.apply_logical_mutation(
            file_path,
            target_method="target",
            target_parameter_count=1,
        )

        with open(file_path, "r", encoding="utf-8") as handle:
            mutated = handle.read()

        self.assertIn("return ready;", mutated)
        self.assertNotIn("return !ready;", mutated)
        self.assertEqual(result["old_operator"], "!")
        self.assertEqual(result["new_operator"], "")
        self.assertEqual(result["mutation_variant"], "unary_not_removal")


if __name__ == "__main__":
    unittest.main()
