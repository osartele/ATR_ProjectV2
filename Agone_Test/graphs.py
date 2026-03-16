import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import os
import json
import utils
import warnings
from collections import OrderedDict




warnings.filterwarnings("ignore")

supported_test_types = ['human', 'evosuite', 'gpt-4o-mini']
supported_techniques = ['zeroshot2', 'oneshot2']

def create_graphs_mean_filtered(output_agone_mean_filtered_path, output_dir):
    """
    This function generates the graphs based on the 'ouput_agone_mean_filtered.csv' file.
    The graphs illustrate the compilation rate and the mean values of code coverage (Branch, Line, and Method), strong mutation coverage, and number of test smells of each test type and technique. 
    Parameters:
                output_agone_mean_filtered_path: the path of the 'output_agone_mean_filtered.csv' file.
                output_dir: the path to save the graphs.
    Returns:
                : False if an error occured, True otherwise.

    """
    output_agone_mean_filtered_df = pd.read_csv(output_agone_mean_filtered_path)
    test_types = output_agone_mean_filtered_df['Test_type'].unique()
    techniques = output_agone_mean_filtered_df['Prompt_Technique'].unique()
    test_types = test_types[test_types != '-']
    techniques = techniques[techniques != '-']
    elements = set() # contains the strings representing test types and techniques.
    values_dict = OrderedDict() # it has 'element' as the key and a dictionary as the value, which represents the compilation rate and the mean values of code coverage, strong mutation coverage, and the number of test smells for each test type and technique.
    for test_type in test_types:
        if test_type != 'human' and test_type != 'evosuite': # if AI test type
            for technique in techniques:
                filter_df = output_agone_mean_filtered_df[(output_agone_mean_filtered_df['Test_type'] == test_type) & (output_agone_mean_filtered_df['Prompt_Technique'] == technique)]
                if filter_df.shape[0] == 1: # if it has only one row
                    new_element = f'{test_type}_{technique}'
                    new_value = OrderedDict()
                    for column, value in filter_df.items():
                        value = value.iloc[0]
                        if column != 'Test_type' and column != 'Prompt_Technique':
                            if pd.notna(value) and isinstance(value, float):
                                new_value[column] = value
                    if len(new_value) != 0:
                        elements.add(new_element)
                        values_dict[new_element] = new_value
                else: 
                    return False
        else:
            new_element = f'{test_type}'
            filter_df = output_agone_mean_filtered_df[(output_agone_mean_filtered_df['Test_type'] == test_type) & (output_agone_mean_filtered_df['Prompt_Technique'] == '-')]
            if filter_df.shape[0] == 1: # if it has only one row
                new_value = OrderedDict()
                new_element = f'{test_type}'
                for column, value in filter_df.items():
                    value = value.iloc[0]
                    if column != 'Test_type' and column != 'Prompt_Technique':
                        if pd.notna(value) and isinstance(value, float):
                            new_value[column] = value
                if len(new_value) != 0:
                    elements.add(new_element)
                    values_dict[new_element] = new_value
            else:
                return False
    elements = list(elements)
    if 'human' in elements:
        elements.remove('human')
        elements.insert(0, 'human')
    if 'evosuite' in elements:
        elements.remove('evosuite')
        elements.insert(1, 'evosuite')
    if os.path.exists(output_dir) == False:
        os.makedirs(output_dir)
    color_dict = OrderedDict()
    for element in elements:
        if element == 'human':
            color_dict[element] = '#ff2e02'
        elif element == 'evosuite':
            color_dict[element] = '#005C99'
        elif element == 'gpt-4o-mini_zeroshot2':
            color_dict[element] = '#9cffb6'
        elif element == 'gpt-4o-mini_oneshot2':
            color_dict[element] = '#34A853'

    if generate_mean_graph_combined_coverage(values_dict, color_dict, output_dir) == True:
        print("Combined graph for code coverage and mutation coverage has been generated successfully!")
    else:
        print("An error occurred while trying to generate the Combined graph for code coverage and mutation coverage!")

    if generate_mean_graphs_single_coverage(values_dict, color_dict, output_dir) == True:
        print("The graphs about the code coverage and mutation coverage have been generated successfully!")
    else:
        print("An error occurred while trying to generate the graphs for the code coverage and mutation coverage!")

    if generate_mean_graph_test_smell(values_dict, color_dict, output_dir) == True:
        print("The graph about the test smell data has been generated successfully!")
    else:
        print("An error occurred while trying to generate the graph about the test smell data!")
    return True


def generate_mean_graph_test_smell(values_dict, color_dict, output_dir):
    """
    This function generates one graph based on the 'ouput_agone_mean_filtered.csv' file.
    The graph illustrates the mean number of test smells of each test type and technique. 
    Parameters:
                value_dict (Dictionary): dictionary containg data about each test type and technique.
                color_dict (Dictionary): dictionary that associates each test type/technique with a color.
                output_dir: the path to save the graphs.
    Returns:
                : False if an error occured, True otherwise.

    """
    try:
        elements = list(values_dict.keys())
        fig_smell, ax_smell = plt.subplots()
        height_smells = OrderedDict()
        for element in elements:
            if element is None:
                continue
            height_smells[element] = values_dict.get(element).get('Num_Test_Smell')
        width_ax_smell = 0.5
        ax_smell.bar(elements, height_smells.values(), width_ax_smell, label=f'Number of test smell', color=color_dict.values())
        ax_smell.set_xticks(elements)
        ax_smell.set_xticklabels(elements, rotation=45, ha='right')
        ax_smell.set_title('Number of test smell')
        ax_smell.set_ylabel('Mean')
        plt.subplots_adjust(bottom=0.25) 
        fig_smell.savefig(f'{output_dir}/graph_smell.png', bbox_inches='tight', dpi=300)
        return True
    except Exception as e:
        return False



def generate_mean_graph_combined_coverage(values_dict, color_dict, output_dir):
    """
    This function generates one graph based on the 'ouput_agone_mean_filtered.csv' file.
    The graph illustrates the compilation rate and the mean values of code coverage (Branch, Line, and Method) and strong mutation coverage of each test type and technique. 
    Parameters:
                value_dict (Dictionary): dictionary containg data about each test type and technique.
                color_dict (Dictionary): dictionary that associates each test type/technique with a color.
                output_dir: the path to save the graphs.
    Returns:
                : False if an error occured, True otherwise.

    """
    try:
        elements = list(values_dict.keys())
        fig_combined_coverage, ax_combined_coverage = plt.subplots()
        width_combined = 0.09
        spacing_combined = 0.02
        for i, element in enumerate(elements):
            values = values_dict.get(element)
            if values is None:
                continue
            color = color_dict.get(element,'#000000')
            values_without_test_smell = {k: v for k, v in values.items() if k != 'Num_Test_Smell'}
            n_values_without_test_smell = len(values_without_test_smell)
            x_single = list(range(n_values_without_test_smell)) 
            heights_coverage = list(values_without_test_smell.values()) 
            # x value for the combined data
            x_combined = [pos + i * (width_combined + spacing_combined) for pos in x_single]
            # Create the graph containing the code coverage data for all the elements
            ax_combined_coverage.bar(x_combined, heights_coverage, width_combined, label=f'{element}', color=color)
        xticks = [pos + width_combined * (len(elements) - 1) / 2 for pos in range(len(values_without_test_smell))]
        ax_combined_coverage.set_xticks(xticks)
        ax_combined_coverage.set_xticklabels(values_without_test_smell.keys(), rotation=25, ha='right')
        ax_combined_coverage.set_ylabel('Percentage')
        ax_combined_coverage.set_title('Combined Bar Graph')
        ax_combined_coverage.tick_params(axis='x', labelsize=8) 
        ax_combined_coverage.legend(loc='upper left', bbox_to_anchor=(1, 1))
        plt.subplots_adjust(bottom=0.25) 
        ax_combined_coverage.set_ylim(0, 100)
        fig_combined_coverage.savefig(f'{output_dir}/combined_graph.png', bbox_inches='tight', dpi=300)
        return True
    except Exception as e:
        return False
        

def generate_mean_graphs_single_coverage(values_dict, color_dict, output_dir):
    """
    This function generates graphs based on the 'ouput_agone_mean_filtered.csv' file.
    Each graph illustrates the compilation rate and the mean values of code coverage (Branch, Line, and Method) and strong mutation coverage for one test type/technique
    Parameters:
                value_dict (Dictionary): dictionary containg data about each test type and technique.
                color_dict (Dictionary): dictionary that associates each test type/technique with a color.
                output_dir: the path to save the graphs.
    Returns:
                : False if an error occured, True otherwise.

    """
    try:
        elements = list(values_dict.keys())
        for i, element in enumerate(elements):
            fig_single_coverage, ax_single_coverage = plt.subplots()
            values = values_dict.get(element)
            if values is None:
                continue
            color = color_dict.get(element,'#000000')
            values_without_test_smell = {k: v for k, v in values.items() if k != 'Num_Test_Smell'}
            n_values_without_test_smell = len(values_without_test_smell)
            x_single = list(range(n_values_without_test_smell)) 
            heights_coverage = list(values_without_test_smell.values()) 
            color = color_dict.get(element,'#000000')

            # Create the graph containing the code coverage data about the single element
            width_ax_single_coverage = 0.5
            ax_single_coverage.bar(x_single, heights_coverage, width_ax_single_coverage, label=f'{element}', color=color)
            ax_single_coverage.set_xticks(x_single)
            ax_single_coverage.set_xticklabels(values_without_test_smell.keys(), rotation=25, ha='right') 
            ax_single_coverage.set_ylabel('Percentage')
            plt.subplots_adjust(bottom=0.25) 
            ax_single_coverage.set_title(f'Graph {element}')
            ax_single_coverage.legend()
            ax_single_coverage.tick_params(axis='x', labelsize=8) 
            ax_single_coverage.set_ylim(0, 100)
            fig_single_coverage.savefig(f'{output_dir}/graph_{element}.png', bbox_inches='tight', dpi=300)
        return True
    except Exception as e:
        return False



def create_graphs_cyclomatic_complexity(output_agone_classes_path, output_agone_classes_filtered_path, output_dir):
    """
    This function generates graphs regarding the cyclomatic complexity of the processed classes.
    The graphs illustrate the relative distribution and the frequency distribution of the cyclomatic complexity.
    Parameters:
                output_agone_classes_path: the path of the Dataframe containing information about the processed classes.
                output_agone_classes_filtered_path: the path of the DataFrame containing the Java classes on which to create the graphs.
                output_dir: the path to save the graphs.
    Returns:
                : False if an error occured, True otherwise.
    """
    try:
        output_agone_classes_df = pd.read_csv(output_agone_classes_path)
        output_agone_classes_filtered_df = pd.read_csv(output_agone_classes_filtered_path)
        java_classes = output_agone_classes_filtered_df['ID_Focal_Class'].unique()
        cyclomatic_complexity_classes= list()
        for java_class in java_classes:
            cyclomatic_complexity_class = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == 'human'),'Cyclomatic_Complexity_Focal_Class'].values[0]
            if cyclomatic_complexity_class is not None and cyclomatic_complexity_class != '-':
                cyclomatic_complexity_class = float(cyclomatic_complexity_class)
                cyclomatic_complexity_classes.append(cyclomatic_complexity_class)
        cyclomatic_complexity_mean = np.mean(np.array(cyclomatic_complexity_classes))
        cyclomatic_complexity_standard_deviation = np.std(np.array(cyclomatic_complexity_classes))
        cyclomatic_complexity_class_max = utils.find_max_value(cyclomatic_complexity_classes)
        cyclomatic_complexity_class_min = utils.find_min_value(cyclomatic_complexity_classes)
        num_bins = 30
        bins = np.linspace(cyclomatic_complexity_class_min, 120, num_bins + 1)  
        cyclomatic_complexity_classes_np = np.array(cyclomatic_complexity_classes)
        counts, _ = np.histogram(cyclomatic_complexity_classes_np, bins=bins)
        x = range(len(counts))  
        y = counts              

        fig, ax = plt.subplots(figsize=(8, 6))
        mean_std_text = f'Mean: {cyclomatic_complexity_mean:.2f}\nStandard deviation: {cyclomatic_complexity_standard_deviation:.2f}'
        props_mean_std_box = dict(boxstyle='round', facecolor='white', alpha=0.5)
        ax.text(1.05, 0.9, mean_std_text, transform=ax.transAxes, fontsize=12,verticalalignment='top', bbox=props_mean_std_box)
        ax.bar(x, y, color='skyblue')
        ax.set_xticks(x)
        ax.set_xticklabels([f'{bins[i]:.1f}-{bins[i+1]:.1f}' for i in x], rotation=90)
        ax.set_title('Distribution of cyclomatic complexity')
        ax.set_xlabel('Cyclomatic complexity')
        ax.set_ylabel('Number of occurrences')
        fig.savefig(f'{output_dir}/graph_distribution_cyclomatic_complexity.png', dpi=300, format='png', bbox_inches='tight')
        print("The graph about the distribution of cyclomatic complexity has been generated successfully!")
    except Exception as e:
        print(e)
        print("An error occured while trying to generate the graph about the distribution of cyclomatic complexity!")
    try:
        absolute_frequencies, edges = np.histogram(cyclomatic_complexity_classes, bins=bins)
        relative_frequencies = (absolute_frequencies / len(cyclomatic_complexity_classes)) * 100
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(1.05, 0.9, mean_std_text, transform=ax.transAxes, fontsize=12,verticalalignment='top', bbox=props_mean_std_box)
        ax.bar(x, relative_frequencies)
        ax.set_xticks(x) 
        ax.set_xticklabels([f'{bins[i]:.1f}-{bins[i+1]:.1f}' for i in x], rotation=90)
        ax.set_xlabel('Cyclomatic Complexity')
        ax.set_ylabel('Relative frequency (percentage)')
        ax.set_title('Relative distribution of cyclomatic complexity')
        fig.savefig(f'{output_dir}/graph_relative_distribution_cyclomatic_complexity.png', dpi=300, format='png', bbox_inches='tight')
        print("The graph about the relative distribution of cyclomatic complexity has been generated successfully!")
    except Exception as e:
        print("An error occured while trying to generate the graph about the relative distribution of cyclomatic complexity!")






def create_graphs_loc(output_agone_classes_path, output_agone_classes_filtered_path, output_dir):
    """
    This function generates graphs regarding the LOC value (Lines OF Code) of the processed classes.
    The graphs illustrate the relative distribution and the frequency distribution of the LOC value.
    Parameters:
                output_agone_classes_path: the path of the Dataframe containing information about the processed classes.
                output_agone_classes_filtered_path: the path of the DataFrame containing the Java classes on which to create the graphs.
                output_dir: the path to save the graphs.
    Returns:
                : False if an error occured, True otherwise.
    """
    try:
        output_agone_classes_df = pd.read_csv(output_agone_classes_path)
        output_agone_classes_filtered_df = pd.read_csv(output_agone_classes_filtered_path)
        java_classes = output_agone_classes_filtered_df['ID_Focal_Class'].unique()
        loc_classes = list()
        for java_class in java_classes:
            loc_class = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == 'human'),'Lines_Of_Code_Focal_Class'].values[0]
            if loc_class is not None and loc_class != '-':
                loc_class = float(loc_class)
                loc_classes.append(loc_class)
        loc_mean = np.mean(np.array(loc_classes))
        loc_standard_deviation = np.std(np.array(loc_classes))
        loc_class_max = utils.find_max_value(loc_classes)
        loc_class_min = utils.find_min_value(loc_classes)
        num_bins = 30
        bins = np.linspace(loc_class_min, loc_class_max, num_bins + 1)  
        loc_classes_np = np.array(loc_classes)
        counts, _ = np.histogram(loc_classes_np, bins=bins)
        x = range(len(counts))  
        y = counts              
        mean_std_text = f'Mean: {loc_mean:.2f}\nStandard deviation: {loc_standard_deviation:.2f}'
        props_mean_std_box = dict(boxstyle='round', facecolor='white', alpha=0.5)
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(1.05, 0.9, mean_std_text, transform=ax.transAxes, fontsize=12,verticalalignment='top', bbox=props_mean_std_box)
        ax.bar(x, y, color='skyblue')
        ax.set_xticks(x)
        ax.set_xticklabels([f'{bins[i]:.1f}-{bins[i+1]:.1f}' for i in x], rotation=90)
        ax.set_title('Distribution of LOC')
        ax.set_xlabel('Lines of Code')
        ax.set_ylabel('Number of occurrences')
        fig.savefig(f'{output_dir}/graph_distribution_loc.png', dpi=300, format='png', bbox_inches='tight')
        print("The graph about the distribution of LOC has been generated successfully!")
    except Exception as e:
        print("An error occured while trying to generate the graph about the distribution of LOC!")
    try:
        absolute_frequencies, edges = np.histogram(loc_classes, bins=bins)
        relative_frequencies = (absolute_frequencies / len(loc_classes)) * 100
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(1.05, 0.9, mean_std_text, transform=ax.transAxes, fontsize=12,verticalalignment='top', bbox=props_mean_std_box)
        ax.bar(x, relative_frequencies)
        ax.set_xticks(x) 
        ax.set_xticklabels([f'{bins[i]:.1f}-{bins[i+1]:.1f}' for i in x], rotation=90)
        ax.set_title('Relative distribution of LOC')
        ax.set_xlabel('Lines of Code')
        ax.set_ylabel('Relative frequency (percentage)')
        fig.savefig(f'{output_dir}/graph_relative_distribution_loc.png', dpi=300, format='png', bbox_inches='tight')
        print("The graph about the relative distribution of LOC has been generated successfully!")
    except Exception as e:
        print("An error occured while trying to generate the graph about the relative distribution of LOC!")




def create_scatterplots_loc_coverage(output_agone_classes_path, output_agone_classes_filtered_path, output_dir):
    try:
        output_agone_classes_df = pd.read_csv(output_agone_classes_path)
        output_agone_classes_filtered_df = pd.read_csv(output_agone_classes_filtered_path)
        java_classes = output_agone_classes_filtered_df['ID_Focal_Class'].unique()
        java_class_loc_dict = OrderedDict()
        for java_class in java_classes:
            loc_class = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == 'human'),'Lines_Of_Code_Focal_Class'].values[0]
            if loc_class is not None and loc_class != '-':
                loc_class = float(loc_class)
                java_class_loc_dict[java_class] = loc_class
        loc_classes = java_class_loc_dict.values()
        loc_class_max = utils.find_max_value(list(loc_classes))
        loc_class_min = utils.find_min_value(list(loc_classes))
        num_bins = 30
        bins = np.linspace(loc_class_min, loc_class_max, num_bins + 1)
        #with bins
        create_scatterplot_coverage_bins(num_bins, bins, java_class_loc_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Branch', 'Line Of Code')
        create_scatterplot_coverage_bins(num_bins, bins, java_class_loc_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Line', 'Line Of Code') 
        create_scatterplot_coverage_bins(num_bins, bins, java_class_loc_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Method', 'Line Of Code') 
        create_scatterplot_coverage_bins(num_bins, bins, java_class_loc_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Compilation', 'Line Of Code') 
        #without bins
        create_scatterplot_coverage(output_agone_classes_path, java_class_loc_dict, 'Branch', 'Line Of Code', output_dir, supported_test_types, supported_techniques)
        create_scatterplot_coverage(output_agone_classes_path, java_class_loc_dict, 'Line', 'Line Of Code', output_dir, supported_test_types, supported_techniques)
        create_scatterplot_coverage(output_agone_classes_path, java_class_loc_dict, 'Method', 'Line Of Code', output_dir, supported_test_types, supported_techniques)
        create_scatterplot_coverage(output_agone_classes_path, java_class_loc_dict, 'Compilation', 'Line Of Code', output_dir, supported_test_types, supported_techniques)


    except Exception as e:
        print(f"An error occured while trying to generate the scatterplots for the LOC (Line OF Code)!")



def create_scatterplots_cyclomatic_complexity_coverage(output_agone_classes_path, output_agone_classes_filtered_path, output_dir):
    try:
        output_agone_classes_df = pd.read_csv(output_agone_classes_path)
        output_agone_classes_filtered_df = pd.read_csv(output_agone_classes_filtered_path)
        java_classes = output_agone_classes_filtered_df['ID_Focal_Class'].unique()
        java_class_cyclomatic_complexity_dict = OrderedDict()
        for java_class in java_classes:
            cyclomatic_complexity_class = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == 'human'),'Cyclomatic_Complexity_Focal_Class'].values[0]
            if cyclomatic_complexity_class is not None and cyclomatic_complexity_class != '-':
                cyclomatic_complexity_class = float(cyclomatic_complexity_class)
                java_class_cyclomatic_complexity_dict[java_class] = cyclomatic_complexity_class
        cyclomatic_complexity_classes = java_class_cyclomatic_complexity_dict.values()
        cyclomatic_complexity_class_max = utils.find_max_value(list(cyclomatic_complexity_classes))
        cyclomatic_complexity_class_min = utils.find_min_value(list(cyclomatic_complexity_classes))
        num_bins = 30
        bins = np.linspace(cyclomatic_complexity_class_min, 120, num_bins + 1)
        #with bins
        create_scatterplot_coverage_bins(num_bins, bins, java_class_cyclomatic_complexity_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Branch', 'Cyclomatic complexity')
        create_scatterplot_coverage_bins(num_bins, bins, java_class_cyclomatic_complexity_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Line', 'Cyclomatic complexity') 
        create_scatterplot_coverage_bins(num_bins, bins, java_class_cyclomatic_complexity_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Method', 'Cyclomatic complexity') 
        create_scatterplot_coverage_bins(num_bins, bins, java_class_cyclomatic_complexity_dict, output_agone_classes_path, output_dir, supported_test_types, supported_techniques, 'Compilation', 'Cyclomatic complexity') 
        #without bins
        create_scatterplot_coverage(output_agone_classes_path, java_class_cyclomatic_complexity_dict, 'Branch', 'Cyclomatic complexity', output_dir, supported_test_types, supported_techniques)
        create_scatterplot_coverage(output_agone_classes_path, java_class_cyclomatic_complexity_dict, 'Line', 'Cyclomatic complexity', output_dir, supported_test_types, supported_techniques)
        create_scatterplot_coverage(output_agone_classes_path, java_class_cyclomatic_complexity_dict, 'Method', 'Cyclomatic complexity', output_dir, supported_test_types, supported_techniques)
        create_scatterplot_coverage(output_agone_classes_path, java_class_cyclomatic_complexity_dict, 'Compilation', 'Cyclomatic complexity', output_dir, supported_test_types, supported_techniques)
    except Exception as e:
        print(f"An error occured while trying to generate the scatterplots for the cyclomatic complexity!")


def create_scatterplot_coverage(output_agone_classes_path, java_class_parameters_dict, criterion, parameter, output_dir, test_types, techniques):
    output_no_bins = os.path.join(output_dir, 'no_bins')
    if not os.path.exists(output_no_bins):
        os.makedirs(output_no_bins)
    output_parameter = os.path.join(output_no_bins, f'{parameter}')
    if not os.path.exists(output_parameter):
        os.makedirs(output_parameter)
    output_criterion = os.path.join(output_parameter, f'{criterion}')
    if not os.path.exists(output_criterion):
        os.makedirs(output_criterion)

    java_classes = list(java_class_parameters_dict.keys())
    for test_type in test_types:
        try:
            if test_type == 'human' or test_type == 'evosuite': # if no AI test type
                coverage_values = list()
                java_class_parameters_dict_copy = java_class_parameters_dict.copy()
                fig, ax = plt.subplots(figsize=(8, 6))
                for java_class in java_classes:
                    coverage_value = search_coverage_java_class(java_class, test_type, criterion, output_agone_classes_path)
                    if coverage_value is not None:
                        coverage_values.append(float(coverage_value))
                    else:
                        java_class_parameters_dict_copy.pop(java_class)
                parameters_values = list(java_class_parameters_dict_copy.values())
                ax.scatter(parameters_values, coverage_values, color='blue', alpha=0.7)
                if criterion != 'Compilation':
                    ax.set_title(f'Scatterplot between {parameter} and {criterion} coverage for the {test_type} test type')
                    ax.set_ylabel(f'{criterion} Coverage')
                    ax.set_ylim(-0.99, 100.99)
                else:
                    ax.set_title(f'Scatterplot between {parameter} and {criterion} percentage for the {test_type} test type')
                    ax.set_ylabel(f'{criterion} percentage')
                    ax.set_yticks([0, 1])
                    ax.margins(y=0.05)
                ax.set_xlabel(f'{parameter}')
                ax.grid()
                fig.savefig(f'{output_criterion}/scatterplot_{test_type}.png', dpi=300, format='png', bbox_inches='tight')  
                print(f"The scatterplot about the correlation between {parameter} and {criterion} coverage for {test_type} test type has been generated successfully!")
            else: # if AI test type
                for technique in techniques:
                    coverage_values = list()
                    java_class_parameters_dict_copy = java_class_parameters_dict.copy()
                    fig, ax = plt.subplots(figsize=(8, 6))
                    for java_class in java_classes:
                        coverage_value = search_coverage_java_class(java_class, test_type, criterion, output_agone_classes_path, technique)
                        if coverage_value is not None:
                            coverage_values.append(float(coverage_value))
                        else:
                            java_class_parameters_dict_copy.pop(java_class)
                    parameters_values = list(java_class_parameters_dict_copy.values())                  
                    ax.scatter(parameters_values, coverage_values, color='blue', alpha=0.7)
                    if criterion != 'Compilation':
                        ax.set_title(f'Scatterplot between {parameter} and {criterion} coverage for the {test_type}_{technique} test type')
                        ax.set_ylabel(f'{criterion} Coverage')
                        ax.set_ylim(-0.99, 100.99)
                    else:
                        ax.set_title(f'Scatterplot between {parameter} and {criterion} percentage for the {test_type}_{technique} test type')
                        ax.set_ylabel(f'{criterion} percentage')
                        ax.set_yticks([0, 1])
                        ax.margins(y=0.05)
                    ax.set_xlabel(f'{parameter}')
                    ax.grid()
                    fig.savefig(f'{output_criterion}/scatterplot_{test_type}_{technique}.png', dpi=300, format='png', bbox_inches='tight')  
                    print(f"The scatterplot about the correlation between {parameter} and {criterion} coverage for {test_type}_{technique} test type has been generated successfully!")
        except Exception as e:
            print(e)
            if criterion != 'Compilation':
                print(f'An errore occured while trying to generate the scatterplot about the correlation between {parameter} and {criterion} coverage for {test_type} test type')
            else:
                print(f'An errore occured while trying to generate the scatterplot about the correlation between {parameter} and {criterion} percentage for {test_type} test type')

                
 
      
                    
                


def create_scatterplot_coverage_bins(num_bins, bins, java_class_paramters_dict, output_agone_classes_path, output_dir, test_types, techniques, criterion, parameter):
    java_classes = list(java_class_paramters_dict.keys())
    output_bins = os.path.join(output_dir, 'bins')
    if not os.path.exists(output_bins):
        os.makedirs(output_bins)
    output_parameter = os.path.join(output_bins, f'{parameter}')
    if not os.path.exists(output_parameter):
        os.makedirs(output_parameter)
    output_criterion = os.path.join(output_parameter, f'{criterion}')
    if not os.path.exists(output_criterion):
        os.makedirs(output_criterion)
    
    for test_type in test_types:
            try:
                if test_type == 'human' or test_type == 'evosuite': # if no AI test type
                    coverage_test_type_dict = OrderedDict()
                    coverage_test_type_dict = {tuple(bins[i:i+2]): [] for i in range(num_bins)}
                    for j in range(num_bins):
                        coverage_test_type_values = list()
                        for java_class in java_classes:
                            parameter_associated = java_class_paramters_dict.get(java_class)
                            if parameter_associated is not None and parameter_associated != '-':
                                if j != num_bins - 1:
                                    if bins[j] <= parameter_associated < bins[j + 1]:
                                        coverage_test_type = search_coverage_java_class(java_class, test_type, criterion, output_agone_classes_path)
                                        if coverage_test_type is not None:
                                            coverage_test_type_values.append(coverage_test_type)
                                else: #if last bins
                                    if bins[j] <= parameter_associated <= bins[j + 1]:
                                        coverage_test_type= search_coverage_java_class(java_class, test_type, criterion, output_agone_classes_path)
                                        if coverage_test_type is not None:
                                            coverage_test_type_values.append(coverage_test_type)          

                        if len(coverage_test_type_values) != 0:
                            coverage_test_type_dict[tuple(bins[j:j + 2])] = (coverage_test_type_values)
                    bin_centers = [0.5 * (k[0] + k[1]) for k in  coverage_test_type_dict.keys()]
                    fig, ax = plt.subplots(figsize=(8, 6))
                    for bin_range, values in coverage_test_type_dict.items():
                        midpoint = 0.5 * (bin_range[0] + bin_range[1])  
                        if criterion != 'Compilation':
                            x_values = [midpoint] * len(values)
                            values_float = [float(value) for value in values]
                            ax.scatter(x_values, values_float, alpha=0.3)
                        elif len(values) != 0:
                            percentage_completition = (values.count(1) / len(values)) * 100
                            ax.scatter(midpoint, percentage_completition, alpha=0.5)
                    if criterion != 'Compilation':
                        ax.set_title(f'Scatterplot between {parameter} and {criterion} coverage for the {test_type} test type')
                        ax.set_ylabel(f'{criterion} Coverage')
                    else:
                        ax.set_title(f'Scatterplot between {parameter} and {criterion} percentage for the {test_type} test type')
                        ax.set_ylabel(f'{criterion} percentage')
                    ax.set_ylim(-0.99, 100.99)
                    ax.set_xlabel(f'{parameter}')
                    ax.set_xticks(bin_centers)  
                    ax.set_xticklabels([f'{center:.2f}' for center in bin_centers], rotation=90)
                    ax.grid()
                    fig.savefig(f'{output_criterion}/scatterplot_{test_type}.png', dpi=300, format='png', bbox_inches='tight')  
                    print(f"The scatterplot about the correlation between {parameter} and {criterion} coverage for {test_type} test type has been generated successfully!")
                else: # if AI test type
                    for technique in techniques:
                        coverage_test_type_dict = OrderedDict()
                        coverage_test_type_dict = {tuple(bins[i:i+2]): [] for i in range(num_bins)}
                        for j in range(num_bins):
                            coverage_test_type_values = list()
                            for java_class in java_classes:
                                if java_class_paramters_dict.get(java_class) is not None and java_class_paramters_dict.get(java_class) != '-':
                                    if j != num_bins - 1:
                                        if bins[j] <= java_class_paramters_dict.get(java_class) < bins[j + 1]:
                                            coverage_test_type = search_coverage_java_class(java_class, test_type, criterion, output_agone_classes_path, technique)
                                            if coverage_test_type is not None:
                                                coverage_test_type_values.append(coverage_test_type)
                                    else: #if last bins
                                        if bins[j] <= java_class_paramters_dict.get(java_class) <= bins[j + 1]:
                                            coverage_test_type= search_coverage_java_class(java_class, test_type, criterion, output_agone_classes_path, technique)
                                            if coverage_test_type is not None:
                                                coverage_test_type_values.append(coverage_test_type)          

                            if len(coverage_test_type_values) != 0:
                                coverage_test_type_dict[tuple(bins[j:j + 2])] = (coverage_test_type_values)
                        bin_centers = [0.5 * (k[0] + k[1]) for k in  coverage_test_type_dict.keys()]
                        fig, ax = plt.subplots(figsize=(8, 6))
                        for bin_range, values in coverage_test_type_dict.items():
                            midpoint = 0.5 * (bin_range[0] + bin_range[1])  
                            if criterion != 'Compilation':
                                x_values = [midpoint] * len(values)
                                values_float = [float(value) for value in values]
                                ax.scatter(x_values, values_float, alpha=0.3)
                            elif len(values) != 0:
                                percentage_completition = (values.count(1) / len(values)) * 100
                                ax.scatter(midpoint, percentage_completition, alpha=0.5)
                        if criterion != 'Compilation':
                            ax.set_title(f'Scatterplot between {parameter} and {criterion} coverage for the {test_type}_{technique} test type')
                            ax.set_ylabel(f'{criterion} Coverage')
                        else:
                            ax.set_title(f'Scatterplot between {parameter} and {criterion} percentage for the {test_type}_{technique} test type')
                            ax.set_ylabel(f'{criterion} percentage')
                        ax.set_ylim(-0.99, 100.99)
                        ax.set_xlabel(f'{parameter}')
                        ax.set_xticks(bin_centers)  
                        ax.set_xticklabels([f'{center:.2f}' for center in bin_centers], rotation=90)
                        ax.grid()
                        fig.savefig(f'{output_criterion}/scatterplot_{test_type}_{technique}.png', dpi=300, format='png', bbox_inches='tight')  
                        print(f"The scatterplot about the correlation between {parameter} and {criterion} coverage for {test_type}_{technique} test type has been generated successfully!")

            except Exception as e:
                print(e)
                print(f'An errore occured while trying to generate the scatterplot about the correlation between {parameter} and line coverage for {test_type} test type')
 
      



 
def search_coverage_java_class(java_class, test_type, coverage_criterion, output_agone_classes_path, technique = None):
    output_agone_classes_df = pd.read_csv(output_agone_classes_path)
    try:
        if technique is None:
            if coverage_criterion != 'Compilation':
                coverage = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type),f'{coverage_criterion}_Coverage'].values[0]
                if coverage is not None and not pd.isna(coverage) and coverage != '-':
                    return coverage
            else:
                coverage = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type),f'{coverage_criterion}'].values[0]
                if coverage is not None and not pd.isna(coverage) and coverage != '-':
                    return coverage
                     
        else:
            if coverage_criterion != 'Compilation':
                coverage = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type) & (output_agone_classes_df['Prompt_Technique'] == technique),f'{coverage_criterion}_Coverage'].values[0]
                if coverage is not None and not pd.isna(coverage) and coverage != '-':
                    return coverage
            else:
                coverage = output_agone_classes_df.loc[(output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type) & (output_agone_classes_df['Prompt_Technique'] == technique),f'{coverage_criterion}'].values[0]
                if coverage is not None and not pd.isna(coverage) and coverage != '-':
                    return coverage
    except Exception as e:
        return None



def create_graphs_info_project(output_agone_projects_filtered_path, project_info_path):
    output_agone_projects_filtered_df = pd.read_csv(output_agone_projects_filtered_path)
    with open(project_info_path, 'r') as file:
        project_info = json.load(file)
    projects = output_agone_projects_filtered_df['Project'].unique()
    java_versions_dict = OrderedDict()
    for project in projects:
        project = str(project)
        java_version = project_info[project].get("java_version")
        java_version = convert_java_version(java_version)
        if java_version != 'null':
            if java_version in java_versions_dict.keys():
                current_count = java_versions_dict[java_version]
                java_versions_dict[java_version] = current_count + 1
            else:
                java_versions_dict[java_version] = 1
    try:
        fig, ax = plt.subplots()
        java_versions_label = list(java_versions_dict.keys())
        java_versions_label = ['Java version ' + s for s in java_versions_label]
        ax.pie(java_versions_dict.values(), labels=java_versions_label, autopct='%1.1f%%', startangle=90)
        ax.axis('equal')
        plt.savefig('output/graphs/java_versions_pie.png', format='png', dpi=300)
        print(f"The pie graph about the java versions of the projects has been generated successfully!")
    except Exception as e:
        print(e)
        print(f"An error occured while trying to generate the pie graph about the java versions of the projects!")
        return False
    return True


def convert_java_version(java_version):
    if java_version == '9':
        return '1.9'
    elif java_version == '8':
        return '1.8'
    elif java_version == '7':
        return '1.7'
    elif java_version == '6':
        return '1.6'
    elif java_version == '5':
        return '1.5'
    return java_version
    





       
             
    


        

         
def main():
    output_agone_mean_filtered_path = 'output/output_agone_mean_filtered.csv'
    output_agone_projects_path = 'output/output_agone_projects.csv'
    output_agone_projects_filtered_path = 'output/output_agone_projects_filtered.csv'
    output_agone_classes_path = 'output/output_agone_classes.csv'
    output_agone_classes_filtered_path = 'output/output_agone_classes_filtered.csv'
    output_dir = 'output/graphs'
    project_info_path = 'output/project_info.json'
    create_graphs_mean_filtered(output_agone_mean_filtered_path, output_dir) 
    create_graphs_cyclomatic_complexity(output_agone_classes_path, output_agone_classes_filtered_path, output_dir)
    create_graphs_loc(output_agone_classes_path, output_agone_classes_filtered_path, output_dir)
    create_scatterplots_cyclomatic_complexity_coverage(output_agone_classes_path, output_agone_classes_filtered_path, output_dir)
    create_scatterplots_loc_coverage(output_agone_classes_path, output_agone_classes_filtered_path, output_dir)
    create_graphs_info_project(output_agone_projects_filtered_path, project_info_path)




if __name__ == "__main__":
    main()