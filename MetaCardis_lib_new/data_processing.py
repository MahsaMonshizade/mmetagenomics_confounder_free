# data_processing.py

import numpy as np
import pandas as pd
import torch
import random
import torch
from torch.utils.data import Dataset

def set_seed(seed):
    """Set seed for reproducibility."""
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)  # If using GPU
    random.seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

def load_and_transform_data(file_path):
    """
    Load data from CSV, apply CLR transformation, and return transformed DataFrame with 'uid'.
    """
    data = pd.read_csv(file_path)
    uid = data['SampleID']
    X = data.drop(columns=['SampleID']).values
    X_log = np.log(X + 1)
    X_log_df = pd.DataFrame(X_log, columns=data.columns[1:])
    X_log_df['SampleID'] = uid
    return X_log_df[['SampleID'] + list(X_log_df.columns[:-1])]


class DiseaseDataset(Dataset):
    def __init__(self, relative_abundance, metadata, device='cpu'):
        # Filter to disease samples
        metadata = metadata[metadata['PATGROUPFINAL_C'] == 1].reset_index(drop=True)
        self.device = device

        # Merge features
        relative_abundance = relative_abundance[relative_abundance['SampleID'].isin(metadata['SampleID'])]
        relative_abundance = relative_abundance.set_index('SampleID').reindex(metadata['SampleID']).reset_index()
        features = relative_abundance.drop(columns=['SampleID']).values

        # Store tensors
        self.features = torch.tensor(features, dtype=torch.float32).to(self.device)
        self.labels = torch.tensor(metadata['METFORMIN_C'].values, dtype=torch.float32).to(self.device)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.features[idx], self.labels[idx]

class MixedDataset(Dataset):
    def __init__(self, relative_abundance, metadata, device='cpu'):
        # Preprocess metadata
        self.device = device

        # Merge features
        relative_abundance = relative_abundance[relative_abundance['SampleID'].isin(metadata['SampleID'])]
        relative_abundance = relative_abundance.set_index('SampleID').reindex(metadata['SampleID']).reset_index()
        features = relative_abundance.drop(columns=['SampleID']).values

        # Store tensors
        self.features = torch.tensor(features, dtype=torch.float32).to(self.device)
        self.labels = torch.tensor(metadata['PATGROUPFINAL_C'].values, dtype=torch.float32).to(self.device)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.features[idx], self.labels[idx]



def dcor_calculation_data(relative_abundance, metadata, device='cpu'):
    """
    Prepare data for distance correlation calculation.
    """

    # Control group
    ctrl_metadata = metadata[metadata['PATGROUPFINAL_C'] == 0]
    ctrl_run_ids = ctrl_metadata['SampleID']
    ctrl_relative_abundance = relative_abundance[relative_abundance['SampleID'].isin(ctrl_run_ids)]
    ctrl_idx = ctrl_metadata.index.tolist()
    training_feature_ctrl = ctrl_relative_abundance.loc[ctrl_idx].drop(columns=['SampleID'])
    metadata_ctrl = ctrl_metadata.loc[ctrl_idx]

    training_feature_ctrl = torch.tensor(training_feature_ctrl.values, dtype=torch.float32).to(device)
    metadata_ctrl_drug = torch.tensor(metadata_ctrl['METFORMIN_C'].values, dtype=torch.float32)
    # Disease group
    disease_metadata = metadata[metadata['PATGROUPFINAL_C'] == 1]
    disease_run_ids = disease_metadata['SampleID']
    disease_relative_abundance = relative_abundance[relative_abundance['SampleID'].isin(disease_run_ids)]
    disease_idx = disease_metadata.index.tolist()
    training_feature_disease = disease_relative_abundance.loc[disease_idx].drop(columns=['SampleID'])
    metadata_disease = disease_metadata.loc[disease_idx]

    training_feature_disease = torch.tensor(training_feature_disease.values, dtype=torch.float32).to(device)
    metadata_disease_drug = torch.tensor(metadata_disease['METFORMIN_C'].values, dtype=torch.float32)

    return (training_feature_ctrl, metadata_ctrl_drug, 
            training_feature_disease, metadata_disease_drug)