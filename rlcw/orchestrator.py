import random

import numpy as np
import torch

import evaluator as eval
import logger
import util
from agents.abstract_agent import AbstractAgent, CheckpointAgent
from runners import RunnerFactory


class Orchestrator:

    def __init__(self, env, agent, config, episodes_to_save, seed: int = 42):
        self.LOGGER = logger.init_logger("Orchestrator")

        self.env = env
        self.agent = agent
        self.config = config
        self.episodes_to_save = episodes_to_save
        self.seed = seed

        # checkpoint stuff
        self.should_save_checkpoints = config["overall"]["checkpoint"]["save"]["enabled"]
        self.save_checkpoint_history = config["overall"]["checkpoint"]["save"]["history"]

        self.should_load_from_checkpoint = config["overall"]["checkpoint"]["load"]["enabled"]
        self.should_use_latest_run_for_load = config["overall"]["checkpoint"]["load"]["use_latest"]
        self.load_directory = config["overall"]["checkpoint"]["load"]["custom_dir"]

        # runner stuff
        self.should_render = config["overall"]["output"]["render"]

        self.max_episodes = config["overall"]["episodes"]["max"]

        self.max_timesteps = config["overall"]["timesteps"]["max"]
        self.start_training_timesteps = config["overall"]["timesteps"]["start_training"]
        self.training_ctx_capacity = config["overall"]["context_capacity"]

        self.runner_factory = RunnerFactory()
        self.time_taken = 0.
        self.results = None

        # eval stuff
        _save_cfg = config["overall"]["output"]["save"]

        self.should_save_raw = _save_cfg["raw"]
        self.should_save_charts = _save_cfg["charts"]
        self.should_save_csv = _save_cfg["csv"]

        self.evaluator = None

        self._sync_seeds()

    def load(self):
        loader = Loader(enabled=self.should_load_from_checkpoint,
                        agent_name=self.agent.name(),
                        use_latest=self.should_use_latest_run_for_load,
                        path=self.load_directory)

        loader.load(self.agent)

    def run(self):
        runner = self.runner_factory.get_runner(self.env, self.agent, self.seed,
                                                episodes_to_save=self.episodes_to_save,
                                                should_render=self.should_render,
                                                max_timesteps=self.max_timesteps,
                                                max_episodes=self.max_episodes,
                                                start_training_timesteps=self.start_training_timesteps,
                                                training_ctx_capacity=self.training_ctx_capacity,
                                                should_save_checkpoints=self.should_save_checkpoints)

        self.LOGGER.info(f'Running agent {self.agent.name()} ...')
        self.results = runner.run()
        # self.time_taken = end - start
        # self.LOGGER.info(f'Time Taken: {self.time_taken}')
        self.env.close()

        if self.should_save_raw:
            self.results.save_to_disk()

    def eval(self):
        self.evaluator = eval.Evaluator(self.results, self.should_save_charts, self.should_save_csv,
                                        agent_name=self.agent.name())
        self.evaluator.eval()

    def _sync_seeds(self):
        np.random.seed(self.seed)
        random.seed(self.seed)
        torch.random.manual_seed(self.seed)
        torch.cuda.manual_seed(self.seed)


class Loader:
    def __init__(self, enabled, agent_name, use_latest, path):
        self.LOGGER = logger.init_logger("CheckpointLoader")

        self.is_enabled = enabled
        self.path = util.get_latest_policies_for(agent_name) if use_latest else path

    def load(self, agent):
        if self.is_enabled:
            if not isinstance(agent, CheckpointAgent):
                self.LOGGER.warning("Can't load checkpoints for this agent! Disabling...")
            else:
                self.LOGGER.info(f"Loading enabled! Loading from {self.path}...")
                agent.load(self.path)


class Results:
    """
    idk how this is going to interact with pytorch cuda parallel stuff, so maybe we'll have to forget this? atm,
    this is responsible for recording results.
    """

    class Timestep:
        def __init__(self, state, action, reward):
            self.state = state
            self.action = action
            self.reward = reward

        def __repr__(self):
            return f'<s: {self.state}, a: {self.action}, r: {self.reward}>'

        def clone(self):
            return Results.Timestep(self.state, self.action, self.reward)

    def __init__(self, agent_name, date_time):
        self.agent_name = agent_name
        self.date_time = date_time

        self.timestep_buffer = []
        self.curr_episode = 0

        self.results = []

        self.results_detailed = {}

    def __repr__(self):
        return self.results.__str__()

    def add(self, episode: int, timestep: Timestep, store_detailed: bool):
        if episode == self.curr_episode:
            self.timestep_buffer.append(timestep)
            return None
        else:
            if store_detailed:
                self.results_detailed[episode] = [t.clone() for t in self.timestep_buffer]

            self.curr_episode = episode

            rewards = np.fromiter(map(lambda t: t.reward, self.timestep_buffer), dtype=float)
            cumulative = np.sum(rewards)
            avg = np.average(rewards)
            no_timesteps = rewards.size

            episode_summary = (cumulative, avg, no_timesteps)
            self.results.append(episode_summary)
            # flush buffer
            self.timestep_buffer = []

            return episode_summary

    def save_to_disk(self):
        file_name = f'{self.agent_name} - {self.date_time}'
        util.save_file("results", file_name, self.results.__str__())
