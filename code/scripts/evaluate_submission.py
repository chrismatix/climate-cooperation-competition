# Copyright (c) 2022, salesforce.com, inc.
# All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
# For full license text, see the LICENSE file in the repo root
# or https://opensource.org/licenses/BSD-3-Clause

"""
Evaluation script for the rice environment
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from collections import OrderedDict

import numpy as np
import yaml

_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.append(os.path.join(_ROOT_DIR, "scripts"))

# Set logger level e.g., DEBUG, INFO, WARNING, ERROR.
logging.getLogger().setLevel(logging.INFO)

_NUM_ROLLOUT_EPISODES = 5
_SEED = 123456

_METRICS_TO_LABEL_DICT = OrderedDict()
# Read the dict values below as
# (label, decimal points used to round off value: 0 becomes an integer)
_METRICS_TO_LABEL_DICT["reward_all_regions"] = ("Total Episode Reward", 2)
_METRICS_TO_LABEL_DICT["global_temperature"] = ("Global Temperature Rise", 2)
_METRICS_TO_LABEL_DICT["global_carbon_mass"] = ("Global Carbon Mass", 0)
_METRICS_TO_LABEL_DICT["capital_all_regions"] = ("Total Capital", 0)
_METRICS_TO_LABEL_DICT["production_all_regions"] = ("Total Production", 0)
_METRICS_TO_LABEL_DICT["gross_output_all_regions"] = ("Total Gross Output", 0)
_METRICS_TO_LABEL_DICT["investment_all_regions"] = ("Total Investment", 0)
_METRICS_TO_LABEL_DICT["abatement_cost_all_regions"] = ("Total Abatement Cost", 2)


def get_imports(framework=None):
    """
    Fetch relevant imports.
    """
    assert framework is not None
    if framework == "rllib":
        from train_with_rllib import (
            create_trainer,
            fetch_episode_states,
            load_model_checkpoints,
        )
    elif framework == "warpdrive":
        from train_with_warp_drive import (
            create_trainer,
            fetch_episode_states,
            load_model_checkpoints,
        )
    else:
        raise ValueError(f"Unknown framework {framework}!")
    return create_trainer, load_model_checkpoints, fetch_episode_states


def get_results_dir():
    """
    Obtain the 'results' directory from the system arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results_dir",
        "-r",
        type=str,
        default=".",
        help="the directory where all the training files were saved.",
    )
    args = parser.parse_args()

    if "results_dir" not in args:
        raise ValueError(
            "Please provide a results directory to evaluate with the argument -r"
        )
    if not os.path.exists(args.results_dir):
        raise ValueError(
            "The results directory is missing. Please make sure the correct path "
            "is specified!"
        )
    try:
        return args.results_dir, parser
    except Exception as err:
        raise ValueError("Cannot obtain the results directory") from err


def validate_dir(results_dir=None):
    """
    Validate that all the required files are present in the 'results' directory.
    """
    assert results_dir is not None
    framework = None

    files = os.listdir(results_dir)
    if ".warpdrive" in files:
        framework = "warpdrive"
        # Warpdrive was used for training
        for file in [
            "rice.py",
            "rice_helpers.py",
            "rice_cuda.py",
            "rice_step.cu",
            "rice_warpdrive.yaml",
        ]:
            if file not in files:
                success = False
                logging.error(
                    "%s is not present in the results directory: %s!", file, results_dir
                )
                comment = f"{file} is not present in the results directory!"
                break
            success = True
            comment = "Valid submission"
    elif ".rllib" in files:
        framework = "rllib"
        # RLlib was used for training
        for file in ["rice.py", "rice_helpers.py", "rice_rllib.yaml"]:
            if file not in files:
                success = False
                logging.error(
                    "%s is not present in the results directory: %s!", file, results_dir
                )
                comment = f"{file} is not present in the results directory!"
                break
            success = True
            comment = "Valid submission"
    else:
        success = False
        logging.error(
            "Missing identifier file! Either the .rllib or the .warpdrive "
            "file must be present in the results directory: %s",
            results_dir,
        )
        comment = "Missing identifier file!"

    return framework, success, comment


def compute_metrics(fetch_episode_states, trainer, framework, num_episodes=5):
    """
    Generate episode rollouts and compute metrics.
    """
    assert fetch_episode_states is not None
    assert trainer is not None
    available_frameworks = ["rllib", "warpdrive"]
    assert (
        framework in available_frameworks
    ), f"Invalid framework {framework}, should be in f{available_frameworks}."

    # Fetch all the desired outputs to compute various metrics.
    desired_outputs = list(_METRICS_TO_LABEL_DICT.keys())
    episode_states = {}
    eval_metrics = {}
    try:
        for episode_id in range(num_episodes):
            episode_states[episode_id] = fetch_episode_states(trainer, desired_outputs)

        for feature in desired_outputs:
            feature_values = [None for _ in range(num_episodes)]

            if feature == "global_temperature":
                # Get the temp rise for upper strata
                for episode_id in range(num_episodes):
                    feature_values[episode_id] = (
                        episode_states[episode_id][feature][-1, 0]
                        - episode_states[episode_id][feature][0, 0]
                    )

            elif feature == "global_carbon_mass":
                for episode_id in range(num_episodes):
                    feature_values[episode_id] = episode_states[episode_id][feature][
                        -1, 0
                    ]

            else:
                for episode_id in range(num_episodes):
                    feature_values[episode_id] = np.sum(
                        episode_states[episode_id][feature]
                    )

            # Compute mean feature value across episodes
            mean_feature_value = np.mean(feature_values)

            # Formatting the values
            metrics_to_label_dict = _METRICS_TO_LABEL_DICT[feature]
            eval_metrics[metrics_to_label_dict[0]] = perform_format(
                mean_feature_value, metrics_to_label_dict[1]
            )

        success = True
        comment = "Successful submission"
    except Exception as err:
        logging.error(err)
        success = False
        comment = "Could not obtain an episode rollout!"
        eval_metrics = {}

    return success, comment, eval_metrics


def perform_format(val, num_decimal_places):
    """
    Format value to the number of desired decimal points.
    """
    assert num_decimal_places >= 0
    rounded_val = np.round(val, num_decimal_places)
    if num_decimal_places == 0:
        return int(rounded_val)
    return rounded_val


def perform_evaluation(
    results_dir=None,
    num_episodes=5,
    eval_seed=None,
):
    """
    Create the trainer and compute metrics.
    """
    assert results_dir is not None
    eval_metrics = {}
    assert num_episodes > 0

    framework, success, comment = validate_dir(results_dir)
    if success:
        try:
            logging.info("Running unit tests...")
            this_file_dir = os.path.dirname(os.path.abspath(__file__))

            subprocess.run(
                [
                    "python",
                    os.path.join(this_file_dir, "run_unittests.py"),
                    "--results_dir",
                    results_dir,
                ],
                check=True,
            )
            logging.info("DONE")

            create_trainer, load_model_checkpoints, fetch_episode_states = get_imports(
                framework=framework
            )

            logging.info("Performing eval...")

            # Load a run configuration
            config_file = os.path.join(results_dir, f"rice_{framework}.yaml")

            if not os.path.exists(config_file):
                success = False
                comment = f"The run configuration is missing in {results_dir}."

            else:
                with open(config_file, "r", encoding="utf8") as file_ptr:
                    run_config = yaml.safe_load(file_ptr)

                # Create trainer object
                try:
                    trainer, _ = create_trainer(run_config, seed=eval_seed)

                    # Load model checkpoints
                    try:
                        load_model_checkpoints(trainer, results_dir)

                        # Compute metrics
                        try:
                            success, comment, eval_metrics = compute_metrics(
                                fetch_episode_states,
                                trainer,
                                framework,
                                num_episodes=num_episodes,
                            )

                            if framework == "warpdrive":
                                trainer.graceful_close()
                            logging.info("DONE!")

                        except Exception as err:
                            logging.error(err)
                            success = False
                            comment = "Count not fetch episode and compute metrics."

                    except Exception as err:
                        logging.error(err)
                        success = False
                        comment = "Could not load model checkpoints."

                except Exception as err:
                    logging.error(err)
                    success = False
                    comment = "Could not create trainer with the run_config provided."

        except Exception as err:
            logging.error(err)
            success = False
            comment = "Unit tests were not successful."

    return framework, success, eval_metrics, comment


if __name__ == "__main__":
    logging.info("This script performs evaluation of your code.")
    results_dir = get_results_dir()[0]

    # Also handle a zipped results_dir
    if results_dir.endswith(".zip"):
        unzipped_results_dir = os.path.join("/tmp", str(time.time()))
        shutil.unpack_archive(results_dir, unzipped_results_dir)
        results_dir = unzipped_results_dir

    framework_used, succeeded, metrics, comments = perform_evaluation(
        results_dir, eval_seed=_SEED, num_episodes=_NUM_ROLLOUT_EPISODES
    )
    logging.info("Framework Used: %s", framework_used)
    logging.info("Succeeded: %s", succeeded)
    logging.info("Metrics: %f", metrics)
    logging.info("Comments: %s", comments)