#!/usr/bin/env python3
"""
OASIS Twitter simulation runner for trading scenarios.

This script runs as a subprocess, launched by SimulationRunner.
It uses the camel-ai OASIS framework to simulate trader interactions
on a virtual Twitter platform.

Usage:
    python run_simulation.py --config /path/to/simulation_config.json --sim-dir /path/to/sim_dir

Adapted from MiroFish backend/scripts/run_twitter_simulation.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_config(config_path: str) -> dict:
    """Load simulation config from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_profiles_csv(csv_path: str) -> list:
    """Load agent profiles from Twitter CSV."""
    profiles = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            profiles.append(row)
    return profiles


def run_simulation_oasis(config: dict, sim_dir: str, profiles: list):
    """
    Run the full OASIS simulation.

    This is the main simulation loop using the camel-ai OASIS framework.
    """
    from bot.swarm.action_logger import ActionLogger
    from bot.swarm.simulation_ipc import IPCServer

    logger = ActionLogger(sim_dir, platform="twitter")
    ipc = IPCServer(sim_dir)

    max_rounds = config.get("max_rounds", 5)
    initial_posts = config.get("initial_posts", [])
    scenario = config.get("scenario_description", config.get("topic", ""))
    available_actions = config.get("available_actions", [
        "CREATE_POST", "LIKE_POST", "REPOST", "QUOTE_POST", "DO_NOTHING",
    ])

    try:
        # Try to import OASIS
        from camel.models import ModelFactory
        from camel.types import ModelPlatformType, ModelType
        from oasis import generate_twitter_agent_graph
        from oasis.social_platform.platform import Platform

        has_oasis = True
    except ImportError:
        has_oasis = False
        print("[SIMULATION] OASIS not available, running in LLM-only fallback mode")

    ipc.start()
    logger.log_simulation_start(config)

    if has_oasis:
        _run_with_oasis(
            config, sim_dir, profiles, logger, ipc,
            max_rounds, initial_posts, scenario, available_actions,
        )
    else:
        _run_llm_fallback(
            config, sim_dir, profiles, logger, ipc,
            max_rounds, initial_posts, scenario,
        )

    ipc.stop()


def _run_with_oasis(
    config, sim_dir, profiles, logger, ipc,
    max_rounds, initial_posts, scenario, available_actions,
):
    """Run simulation using the full OASIS framework."""
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType, ModelType
    from oasis import generate_twitter_agent_graph

    # Initialize LLM via OASIS ModelFactory
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=config.get("model_name", "gpt-4o-mini"),
        api_key=os.environ.get("SWARM_LLM_API_KEY", ""),
    )

    # Generate agent graph
    agent_graph = generate_twitter_agent_graph(
        agent_info_path=str(Path(sim_dir) / "twitter_profiles.csv"),
        model=model,
    )

    env = agent_graph.env

    # Post seed content
    for post in initial_posts:
        agent_id = post.get("poster_agent_id", 0)
        content = post.get("content", "")
        if content and agent_id < len(agent_graph.agents):
            env.post(agent_id, content)
            logger.log_action(
                round_num=0, agent_id=agent_id,
                agent_name=post.get("poster_name", f"Agent_{agent_id}"),
                action_type="CREATE_POST",
                action_args={"content": content},
                result="seed_post",
            )

    # Main simulation loop
    total_actions = 0
    for round_num in range(1, max_rounds + 1):
        logger.log_round_start(round_num, active_agents=len(profiles))
        round_actions = 0

        # Each agent takes an action
        actions = env.step()

        for agent_id, action_data in enumerate(actions):
            if action_data is None:
                continue

            action_type = str(action_data.get("action", "DO_NOTHING"))
            action_args = action_data.get("args", {})
            agent_name = profiles[agent_id]["name"] if agent_id < len(profiles) else f"Agent_{agent_id}"

            if action_type != "DO_NOTHING":
                logger.log_action(
                    round_num=round_num,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    action_type=action_type,
                    action_args=action_args,
                )
                round_actions += 1

        total_actions += round_actions
        logger.log_round_end(round_num, round_actions)

        # Check for IPC commands
        commands = ipc.poll_commands()
        for cmd in commands:
            if cmd.get("type") == "stop":
                logger.log_simulation_end(total_actions, status="stopped")
                return

        print(f"[SIMULATION] Round {round_num}/{max_rounds}: {round_actions} actions")

    logger.log_simulation_end(total_actions, status="completed")
    print(f"[SIMULATION] Complete: {total_actions} total actions")


def _run_llm_fallback(
    config, sim_dir, profiles, logger, ipc,
    max_rounds, initial_posts, scenario,
):
    """
    Fallback mode: run simulation using direct LLM calls without OASIS.

    Each round, we ask GPT-4o-mini to simulate what each trader would
    do given the current discussion state. This is simpler than OASIS
    but still produces useful sentiment analysis.
    """
    from bot.swarm.llm_client import SwarmLLMClient

    llm = SwarmLLMClient()

    # Build initial feed from seed posts
    feed: list[str] = []
    for post in initial_posts:
        content = post.get("content", "")
        name = post.get("poster_name", "Unknown")
        if content:
            feed.append(f"@{name}: {content}")
            logger.log_action(
                round_num=0,
                agent_id=post.get("poster_agent_id", 0),
                agent_name=name,
                action_type="CREATE_POST",
                action_args={"content": content},
                result="seed_post",
            )

    total_actions = 0

    for round_num in range(1, max_rounds + 1):
        logger.log_round_start(round_num, active_agents=len(profiles))
        round_actions = 0

        # Limit agents per round for cost control
        agents_this_round = config.get("agents_per_round", 6)

        for profile in profiles[:agents_this_round]:
            try:
                # Ask the LLM to simulate this trader's action
                response = llm.chat_json(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                f"{profile['user_char']}\n\n"
                                f"You are participating in a Crypto Twitter discussion about: {scenario}\n\n"
                                f"Current feed:\n" + "\n".join(feed[-15:]) + "\n\n"
                                f"Decide what to do. Return JSON with:\n"
                                f'- "action": one of "CREATE_POST", "LIKE_POST", "REPOST", "QUOTE_POST", "DO_NOTHING"\n'
                                f'- "content": your tweet text (if CREATE_POST or QUOTE_POST)\n'
                                f'- "target_post": which post to interact with (if LIKE/REPOST/QUOTE, use the @username line)\n'
                                f'- "reasoning": 1 sentence explaining your decision\n'
                            ),
                        },
                        {
                            "role": "user",
                            "content": "What do you do this round?",
                        },
                    ],
                    temperature=0.8,
                    max_tokens=300,
                )

                action_type = response.get("action", "DO_NOTHING")
                content = response.get("content", "")
                reasoning = response.get("reasoning", "")

                if action_type == "CREATE_POST" and content:
                    feed.append(f"@{profile['name']}: {content}")

                if action_type != "DO_NOTHING":
                    logger.log_action(
                        round_num=round_num,
                        agent_id=int(profile.get("user_id", 0)),
                        agent_name=profile["name"],
                        action_type=action_type,
                        action_args={
                            "content": content,
                            "reasoning": reasoning,
                        },
                    )
                    round_actions += 1

            except Exception as e:
                print(f"[SIMULATION] Agent {profile['name']} error: {e}")

        total_actions += round_actions
        logger.log_round_end(round_num, round_actions)

        # Check for stop commands
        commands = ipc.poll_commands()
        for cmd in commands:
            if cmd.get("type") == "stop":
                logger.log_simulation_end(total_actions, status="stopped")
                return

        print(f"[SIMULATION] Round {round_num}/{max_rounds}: {round_actions} actions")

    logger.log_simulation_end(total_actions, status="completed")
    print(f"[SIMULATION] Complete: {total_actions} total actions over {max_rounds} rounds")


def main():
    parser = argparse.ArgumentParser(description="Run OASIS trading simulation")
    parser.add_argument("--config", required=True, help="Path to simulation_config.json")
    parser.add_argument("--sim-dir", required=True, help="Simulation data directory")
    args = parser.parse_args()

    config = load_config(args.config)
    sim_dir = args.sim_dir

    # Load profiles
    profiles_path = Path(sim_dir) / "twitter_profiles.csv"
    if profiles_path.exists():
        profiles = load_profiles_csv(str(profiles_path))
    else:
        print("[SIMULATION] No profiles CSV found, using config agent_configs")
        profiles = [
            {"user_id": str(a["agent_id"]), "name": a["entity_name"], "username": a["entity_name"].lower().replace(" ", "_"), "user_char": "", "description": ""}
            for a in config.get("agent_configs", [])
        ]

    print(f"[SIMULATION] Starting: {config.get('topic', 'Unknown topic')}")
    print(f"[SIMULATION] Agents: {len(profiles)}, Rounds: {config.get('max_rounds', 5)}")

    run_simulation_oasis(config, sim_dir, profiles)


if __name__ == "__main__":
    main()
