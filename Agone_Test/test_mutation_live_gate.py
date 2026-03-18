import unittest
from unittest.mock import patch

import pandas as pd
import os
import tempfile

try:
    from Agone_Test import mavenLib
    from Agone_Test import utils
except ModuleNotFoundError:
    import mavenLib
    import utils


def _scoped_dataframe():
    return pd.DataFrame(
        [
            {
                "Project": 1,
                "Focal_Path": "compiledrepos/1/src/main/java/demo/SampleService.java",
                "Test_Path": "compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
                "Test_Class": "demo.SampleServiceTest",
                "Focal_Class": "demo.SampleService",
                "AST_Test_Method": "targetTest",
                "AST_Focal_Method": "targetMethod",
            }
        ]
    )


class MutationLiveGateTests(unittest.TestCase):
    def test_infer_target_focal_parameter_count_from_test_invocation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file_path = os.path.join(temp_dir, "SampleServiceTest.java")
            with open(test_file_path, "w", encoding="utf-8") as test_file:
                test_file.write(
                    "package demo;\n"
                    "import org.junit.Test;\n"
                    "public class SampleServiceTest {\n"
                    "    private SampleService service = new SampleService();\n"
                    "    @Test\n"
                    "    public void targetTest() {\n"
                    "        service.targetMethod(\"x\", 1, true, false);\n"
                    "    }\n"
                    "}\n"
                )

            inferred_count = mavenLib._infer_target_focal_parameter_count(
                test_file_path,
                "targetTest",
                "targetMethod",
            )

        self.assertEqual(inferred_count, 4)

    def test_verify_mutation_live_on_first_failure(self):
        scoped_df = _scoped_dataframe()
        with patch.object(
            mavenLib,
            "run_maven_baseline_stage",
            return_value={"ok": False, "failure_log": "java.lang.AssertionError: expected:<1>, but was:<2>"},
        ):
            result = mavenLib.verify_mutation_is_live(
                project="1",
                maven_execution_path=".",
                scoped_dataframe=scoped_df,
                system="Windows",
                ast_test_method="targetTest",
                ast_focal_method="targetMethod",
                focal_path="compiledrepos/1/src/main/java/demo/SampleService.java",
                test_path="compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
            )

        self.assertTrue(result["is_live"])
        self.assertEqual(result["high_signal"], 1)
        self.assertEqual(result["signal_reason"], "active_mutation")
        self.assertEqual(result["attempts_used"], 0)

    def test_current_mutation_type_filters_by_method_and_arity(self):
        records = [
            {
                "focal_path": "compiledrepos/1/src/main/java/demo/SampleService.java",
                "mutation_type": "exception",
                "method_name": "helperMethod",
                "method_parameter_count": 1,
            },
            {
                "focal_path": "compiledrepos/1/src/main/java/demo/SampleService.java",
                "mutation_type": "logical",
                "method_name": "targetMethod",
                "method_parameter_count": 4,
            },
        ]
        with patch.object(mavenLib, "_load_json_file", return_value=records):
            mutation_type = mavenLib._current_mutation_type_for_focal(
                "1",
                "compiledrepos/1/src/main/java/demo/SampleService.java",
                target_method_name="targetMethod",
                target_parameter_count=4,
            )

        self.assertEqual(mutation_type, "logical")

    def test_current_mutation_type_for_exception_includes_scope_variant(self):
        records = [
            {
                "focal_path": "compiledrepos/1/src/main/java/demo/SampleService.java",
                "mutation_type": "exception",
                "method_name": "targetMethod",
                "method_parameter_count": 4,
                "injection_scope": "catch_block",
            }
        ]
        with patch.object(mavenLib, "_load_json_file", return_value=records):
            mutation_type = mavenLib._current_mutation_type_for_focal(
                "1",
                "compiledrepos/1/src/main/java/demo/SampleService.java",
                target_method_name="targetMethod",
                target_parameter_count=4,
            )

        self.assertEqual(mutation_type, "exception:catch_block")

    def test_retry_mutation_tries_method_entry_after_catch_variant(self):
        calls = []

        def _fake_exception_mutation(
            focal_path,
            target_method=None,
            target_parameter_count=None,
            preferred_injection_scope=None,
        ):
            calls.append(preferred_injection_scope)
            return {
                "mutation_type": "exception",
                "method_name": target_method,
                "method_parameter_count": target_parameter_count,
                "injection_scope": preferred_injection_scope,
            }

        with patch.object(
            mavenLib,
            "MUTATION_RETRY_PRIORITIES",
            [("exception", "ASSERTION_ERROR_FALLBACK", _fake_exception_mutation)],
        ):
            mutation_result, mutation_type, mutation_error = mavenLib._apply_prioritized_retry_mutation(
                "compiledrepos/1/src/main/java/demo/SampleService.java",
                "targetMethod",
                {"exception:catch_block"},
                target_focal_parameter_count=4,
            )

        self.assertEqual(mutation_error, "")
        self.assertEqual(mutation_type, "exception")
        self.assertEqual(calls, ["method_entry"])
        self.assertEqual(mutation_result.get("injection_scope"), "method_entry")
        self.assertEqual(mutation_result.get("mutation_attempt_key"), "exception:method_entry")

    def test_retry_mutation_tries_next_logical_candidate_variant(self):
        calls = []

        def _fake_logical_mutation(
            focal_path,
            target_method=None,
            target_parameter_count=None,
            preferred_candidate_index=None,
        ):
            calls.append(preferred_candidate_index)
            if preferred_candidate_index == 0:
                raise ValueError("logical candidate 0 rejected")
            if preferred_candidate_index == 1:
                return {
                    "mutation_type": "logical",
                    "method_name": target_method,
                    "method_parameter_count": target_parameter_count,
                    "mutation_candidate_index": preferred_candidate_index,
                }
            raise ValueError(f"No logical mutation candidate at index {preferred_candidate_index}.")

        with patch.object(
            mavenLib,
            "MUTATION_RETRY_PRIORITIES",
            [("logical", "NEGATE_CONDITIONALS", _fake_logical_mutation)],
        ):
            mutation_result, mutation_type, mutation_error = mavenLib._apply_prioritized_retry_mutation(
                "compiledrepos/1/src/main/java/demo/SampleService.java",
                "targetMethod",
                set(),
                target_focal_parameter_count=4,
            )

        self.assertEqual(mutation_error, "")
        self.assertEqual(mutation_type, "logical")
        self.assertEqual(calls[:2], [0, 1])
        self.assertEqual(mutation_result.get("mutation_attempt_key"), "logical:1")

    def test_verify_mutation_live_returns_no_context_safe_signal(self):
        scoped_df = _scoped_dataframe()
        baseline_side_effect = [
            {
                "ok": False,
                "failure_log": "java.lang.IllegalStateException: Failed to load ApplicationContext",
            },
            {
                "ok": False,
                "failure_log": "Error creating bean with name 'proxyConfig'",
            },
        ]
        with patch.object(mavenLib, "run_maven_baseline_stage", side_effect=baseline_side_effect), patch.object(
            mavenLib, "_restore_focal_from_backup", return_value=(True, "")
        ), patch.object(
            mavenLib,
            "_apply_prioritized_retry_mutation",
            side_effect=[
                (
                    {
                        "mutation_type": "logical",
                        "method_name": "targetMethod",
                        "mutation_attempt_key": "logical:0",
                    },
                    "logical",
                    "",
                ),
                (None, None, "no prioritized mutation strategies remaining for retry"),
            ],
        ), patch.object(mavenLib, "_upsert_focal_mutation_record"), patch.object(
            mavenLib, "_current_mutation_type_for_focal", return_value=None
        ), patch.object(mavenLib.os.path, "isfile", return_value=True), patch.object(
            mavenLib, "_get_int_run_setting", return_value=2
        ):
            result = mavenLib.verify_mutation_is_live(
                project="1",
                maven_execution_path=".",
                scoped_dataframe=scoped_df,
                system="Windows",
                ast_test_method="targetTest",
                ast_focal_method="targetMethod",
                focal_path="compiledrepos/1/src/main/java/demo/SampleService.java",
                test_path="compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
            )

        self.assertFalse(result["is_live"])
        self.assertEqual(result["high_signal"], 0)
        self.assertEqual(result["signal_reason"], "no_context_safe_active_mutant")

    def test_verify_mutation_live_retries_then_activates(self):
        scoped_df = _scoped_dataframe()
        baseline_side_effect = [
            {"ok": True, "failure_log": ""},
            {"ok": False, "failure_log": "Wanted but not invoked: addNotificationMessageToDatabaseQueue"},
        ]
        with patch.object(mavenLib, "run_maven_baseline_stage", side_effect=baseline_side_effect), patch.object(
            mavenLib, "_restore_focal_from_backup", return_value=(True, "")
        ), patch.object(
            mavenLib,
            "_apply_prioritized_retry_mutation",
            return_value=(
                {"mutation_type": "logical", "method_name": "targetMethod"},
                "logical",
                "",
            ),
        ), patch.object(mavenLib, "_upsert_focal_mutation_record"), patch.object(
            mavenLib, "_current_mutation_type_for_focal", return_value=None
        ), patch.object(mavenLib.os.path, "isfile", return_value=True), patch.object(
            mavenLib, "_get_int_run_setting", return_value=2
        ):
            result = mavenLib.verify_mutation_is_live(
                project="1",
                maven_execution_path=".",
                scoped_dataframe=scoped_df,
                system="Windows",
                ast_test_method="targetTest",
                ast_focal_method="targetMethod",
                focal_path="compiledrepos/1/src/main/java/demo/SampleService.java",
                test_path="compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
            )

        self.assertTrue(result["is_live"])
        self.assertEqual(result["high_signal"], 1)
        self.assertEqual(result["signal_reason"], "active_mutation")
        self.assertEqual(result["attempts_used"], 1)

    def test_verify_mutation_live_quiet_after_retries(self):
        scoped_df = _scoped_dataframe()
        baseline_side_effect = [
            {"ok": True, "failure_log": ""},
            {"ok": True, "failure_log": ""},
            {"ok": True, "failure_log": ""},
        ]
        retry_mutations = [
            ({"mutation_type": "logical", "method_name": "targetMethod"}, "logical", ""),
            ({"mutation_type": "signature", "method_name": "targetMethod"}, "signature", ""),
        ]
        with patch.object(mavenLib, "run_maven_baseline_stage", side_effect=baseline_side_effect), patch.object(
            mavenLib, "_restore_focal_from_backup", return_value=(True, "")
        ), patch.object(
            mavenLib,
            "_apply_prioritized_retry_mutation",
            side_effect=retry_mutations,
        ), patch.object(mavenLib, "_upsert_focal_mutation_record"), patch.object(
            mavenLib, "_current_mutation_type_for_focal", return_value=None
        ), patch.object(mavenLib.os.path, "isfile", return_value=True), patch.object(
            mavenLib, "_get_int_run_setting", return_value=2
        ):
            result = mavenLib.verify_mutation_is_live(
                project="1",
                maven_execution_path=".",
                scoped_dataframe=scoped_df,
                system="Windows",
                ast_test_method="targetTest",
                ast_focal_method="targetMethod",
                focal_path="compiledrepos/1/src/main/java/demo/SampleService.java",
                test_path="compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
            )

        self.assertFalse(result["is_live"])
        self.assertEqual(result["high_signal"], 0)
        self.assertEqual(result["signal_reason"], "quiet_mutation_after_3_attempts")
        self.assertEqual(result["attempts_used"], 2)

    def test_failure_signal_extracts_assertion_anchor(self):
        failure_log = (
            "java.lang.AssertionError: expected:<[abc]>, but was:<[xyz]>\n"
            "at demo.SampleServiceTest.targetTest(SampleServiceTest.java:42)"
        )
        signal = mavenLib._extract_concise_failure_signal(failure_log)
        self.assertIn("java.lang.AssertionError", signal)
        self.assertIn("expected:<[abc]>, but was:<[xyz]>", signal)

    def test_failure_signal_extracts_wanted_not_invoked_anchor(self):
        failure_log = "Wanted but not invoked: addNotificationMessageToDatabaseQueue(...)"
        signal = mavenLib._extract_concise_failure_signal(failure_log)
        self.assertIn("Wanted but not invoked", signal)

    def test_failure_signal_extracts_arguments_different_anchor(self):
        failure_log = (
            "Arguments are different! Wanted: payload=foo\n"
            "Actual: payload=bar\n"
        )
        signal = mavenLib._extract_concise_failure_signal(failure_log)
        self.assertIn("Arguments are different!", signal)
        self.assertIn("Wanted:", signal)
        self.assertIn("Actual:", signal)

    def test_failure_signal_extracts_compilation_anchor(self):
        failure_log = (
            "[ERROR] COMPILATION ERROR :\n"
            "[ERROR] /tmp/SampleServiceTest.java:[12,18] ';' expected\n"
        )
        signal = mavenLib._extract_concise_failure_signal(failure_log)
        self.assertIn("SampleServiceTest.java:[12,18]", signal)

    def test_detects_explicit_surefire_failure_when_exit_code_is_zero(self):
        stdout_text = (
            "[INFO] BUILD SUCCESS\n"
            "[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0\n"
            "[ERROR] There are test failures.\n"
        )
        self.assertTrue(mavenLib._has_explicit_test_failure_output(stdout_text, ""))

    def test_ignores_successful_surefire_summary(self):
        stdout_text = (
            "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"
            "[INFO] BUILD SUCCESS\n"
        )
        self.assertFalse(mavenLib._has_explicit_test_failure_output(stdout_text, ""))

    def test_tracking_lookup_includes_high_signal_fields(self):
        tracking_df = pd.DataFrame(
            [
                {
                    "Test_Class": "demo.SampleServiceTest",
                    "Test_Path": "compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
                    "Generator(LLM/EVOSUITE)": "codex-cli",
                    "Prompt_Technique": "iterative-healing",
                    "Chance": 0,
                    "Total_Prompt_Tokens": 1,
                    "Total_Completion_Tokens": 2,
                    "Iterations_to_Pass": 0,
                    "High_Signal": 0,
                    "Signal_Reason": "quiet_mutation_after_3_attempts",
                }
            ]
        )
        metrics = utils._lookup_tracking_metrics(
            tracking_df,
            "demo.SampleServiceTest",
            "compiledrepos/1/src/test/java/demo/SampleServiceTest.java",
            "codex-cli",
            "iterative-healing",
        )
        self.assertEqual(metrics["High_Signal"], 0)
        self.assertEqual(metrics["Signal_Reason"], "quiet_mutation_after_3_attempts")

    def test_generate_output_keeps_row_for_mavenfailed_technique(self):
        project = "1"
        project_df = _scoped_dataframe()
        test_types = ["codex-cli"]
        techniques = ["iterative-healing"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_output_dir = os.path.join(tmp_dir, project)
            os.makedirs(project_output_dir, exist_ok=True)
            marker_path = os.path.join(
                project_output_dir,
                f"TestClasses_{project}_codex-cli_iterative-healing.mavenfailed",
            )
            with open(marker_path, "w", encoding="utf-8"):
                pass

            with patch.object(utils, "PATH_CONTEXT") as mocked_path_context, patch.object(
                mavenLib, "df_chance", pd.DataFrame()
            ):
                mocked_path_context.get_project_output_path.return_value = project_output_dir
                mocked_path_context.to_worker_compiled_path.side_effect = (
                    lambda current_project, path_value: path_value
                )
                df_output, _ = utils.generate_output_csv_project(
                    project=project,
                    project_dataframe=project_df,
                    test_types=test_types,
                    techniques=techniques,
                )

        self.assertIsNotNone(df_output)
        self.assertFalse(df_output.empty)
        row = df_output[
            (df_output["Generator(LLM/EVOSUITE)"] == "codex-cli")
            & (df_output["Prompt_Technique"] == "iterative-healing")
        ].iloc[0]
        self.assertEqual(str(row["Compilation"]), "0")


if __name__ == "__main__":
    unittest.main()
