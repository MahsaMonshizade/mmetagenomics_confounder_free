# losses.py

import torch
import torch.nn as nn

class CorrelationCoefficientLoss(nn.Module):
    """
    Custom loss function based on the squared correlation coefficient.
    """
    def __init__(self):
        super(CorrelationCoefficientLoss, self).__init__()

    def forward(self, y_true, y_pred):
        mean_x = torch.mean(y_true)
        mean_y = torch.mean(y_pred)
        covariance = torch.mean((y_true - mean_x) * (y_pred - mean_y))
        std_x = torch.std(y_true)
        std_y = torch.std(y_pred)
        eps = 1e-5
        corr = covariance / (std_x * std_y + eps)
        return corr ** 2

class InvCorrelationCoefficientLoss(nn.Module):
    """
    Custom loss function inverse of squared correlation coefficient.
    """
    def __init__(self):
        super(InvCorrelationCoefficientLoss, self).__init__()

    def forward(self, y_true, y_pred):
        mean_x = torch.mean(y_true)
        mean_y = torch.mean(y_pred)
        covariance = torch.mean((y_true - mean_x) * (y_pred - mean_y))
        std_x = torch.std(y_true)
        std_y = torch.std(y_pred)
        eps = 1e-5
        corr = covariance / (std_x * std_y + eps)
        return 1 - corr ** 2
    

import torch

class PearsonCorrelationLoss(torch.nn.Module):
    """
    Differentiable Pearson correlation loss for use in PyTorch models.
    This can be used to compute the correlation between two variables, such as actual and predicted labels.
    """
    def __init__(self):
        super(PearsonCorrelationLoss, self).__init__()

    def forward(self, x, y):
        """
        Forward pass for computing Pearson correlation between x and y.

        :param x: Tensor of actual values (e.g., actual gender, binary)
        :param y: Tensor of predicted values (e.g., predicted gender, can be binary or continuous)
        :return: Pearson correlation coefficient (between -1 and 1)
        """
        # Flatten the tensors to ensure they are 1D
        x = x.view(-1)
        y = y.view(-1)

        # Calculate mean of x and y
        x_mean = torch.mean(x)
        y_mean = torch.mean(y)

        # Center the data by subtracting the mean
        xm = x - x_mean
        ym = y - y_mean

        # Calculate covariance and standard deviations
        cov = torch.sum(xm * ym)
        x_std = torch.sqrt(torch.sum(xm ** 2))
        y_std = torch.sqrt(torch.sum(ym ** 2))

        # Pearson correlation coefficient
        correlation = cov / (x_std * y_std)

        return correlation ** 2