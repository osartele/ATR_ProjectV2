import os
import yaml


class ExecutionManager:
    agents_dict = None
    prompts_dict = None
    yaml_path = os.path.join(os.path.dirname(__file__), 'run_settings.yaml')

    @classmethod
    def initialize(cls):
        """
        Initialize the ExecutionManager by loading data from the YAML file.
        This method populates the agents_dict and prompts_dict class variables.
        """
        print("Initializing ExecutionManager...")
        with open(cls.yaml_path, 'r') as yaml_file:
            yaml_data = yaml.safe_load(yaml_file)
            prompts = yaml_data.get('prompts', [])
            cls.prompts_dict = {prompt['name']: prompt['value'] for prompt in prompts if 'name' in prompt}
            print(f"Loaded prompts: {cls.prompts_dict}")

            agents = yaml_data.get('agents', [])
            cls.agents_dict = {agent['model']: {k: v for k, v in agent.items() if k != 'model'} for agent in agents}
            print(f"Loaded agents: {cls.agents_dict}")

    @classmethod
    def get_agents_list(cls):
        """
        Get a list of all agent models.

        Returns:
            list: A list of agent model names.
        """
        print("Fetching agents list...")
        return list(cls.agents_dict.keys())

    @classmethod
    def get_prompts_list(cls):
        """
        Get a list of all prompt names.

        Returns:
            list: A list of prompt names.
        """
        print("Fetching prompts list...")
        return list(cls.prompts_dict.keys())

    @classmethod
    def get_agents(cls):
        """
        Get the dictionary of agents.

        Returns:
            dict: A dictionary of agents with their configurations.
        """
        print("Fetching agents dictionary...")
        return cls.agents_dict

    @classmethod
    def get_prompts(cls):
        """
        Get the dictionary of prompts.

        Returns:
            dict: A dictionary of prompts with their values.
        """
        print("Fetching prompts dictionary...")
        return cls.prompts_dict
