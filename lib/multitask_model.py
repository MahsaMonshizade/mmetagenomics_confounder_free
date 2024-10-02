# models.py

import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim import lr_scheduler
from torch.nn import init
import numpy as np
from losses import CorrelationCoefficientLoss, InvCorrelationCoefficientLoss
from data_processing import create_batch, dcor_calculation_data
import matplotlib.pyplot as plt
import json
import dcor
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, f1_score
from sklearn.feature_selection import mutual_info_regression
from sklearn.model_selection import StratifiedKFold

# Add this import for GradientReversalFunction
from torch.autograd import Function

def previous_power_of_two(n):
    """Return the greatest power of two less than or equal to n."""
    return 2 ** (n.bit_length() - 1)


class GradientReversalFunction(Function):
    @staticmethod
    def forward(ctx, x):
        return x.view_as(x)
    @staticmethod
    def backward(ctx, grad_output):
        return grad_output.neg()

class GradientReversal(nn.Module):
    def forward(self, x):
        return GradientReversalFunction.apply(x)





class GAN(nn.Module):
    """
    Generative Adversarial Network class.
    """
    def __init__(self, input_dim, latent_dim=64, activation_fn=nn.SiLU, num_layers=1):
        """Initialize the GAN with an encoder, age regressor, BMI regressor, and disease classifier."""
        super(GAN, self).__init__()

        # Store parameters for re-initialization
        self.input_dim = input_dim
        self.latent_dim = latent_dim
        self.activation_fn = activation_fn
        self.num_layers = num_layers

        self.encoder = self._build_encoder(input_dim, latent_dim, num_layers, activation_fn)
        # In the GAN class
        self.grl = GradientReversal()
        # Replace separate regressors with a multi-task regressor
        self.confounder_regressor = self._build_regressor(latent_dim, activation_fn, num_layers)
        self.disease_classifier = self._build_classifier(latent_dim, activation_fn, num_layers)

         # Update loss functions
        self.confounder_regression_loss = nn.MSELoss()
        self.confounder_distiller_loss = nn.MSELoss()
        self.disease_classifier_loss = nn.BCEWithLogitsLoss()

        self.initialize_weights()

    def _build_encoder(self, input_dim, latent_dim, num_layers, activation_fn):
        """Build the encoder network."""
        layers = []
        first_layer = previous_power_of_two(input_dim)
        layers.extend([
            nn.Linear(input_dim, first_layer),
            nn.BatchNorm1d(first_layer),
            activation_fn()
        ])
        current_dim = first_layer
        for _ in range(num_layers):
            layers.extend([
                nn.Linear(current_dim, current_dim // 2),
                nn.BatchNorm1d(current_dim // 2),
                activation_fn()
            ])
            current_dim = current_dim // 2
        layers.extend([
            nn.Linear(current_dim, latent_dim),
            nn.BatchNorm1d(latent_dim),
            activation_fn()
        ])
        return nn.Sequential(*layers)

    def _build_regressor(self, latent_dim, activation_fn, num_layers):
        """Build the age or BMI regressor."""
        layers = []
        current_dim = latent_dim
        for _ in range(num_layers):
            layers.extend([
                nn.Linear(current_dim, current_dim // 2),
                nn.BatchNorm1d(current_dim // 2),
                activation_fn()
            ])
            current_dim = current_dim // 2
        layers.append(nn.Linear(current_dim, 2))
        return nn.Sequential(*layers)

    def _build_classifier(self, latent_dim, activation_fn, num_layers):
        """Build the disease classifier."""
        layers = []
        current_dim = latent_dim
        for _ in range(num_layers):
            layers.extend([
                nn.Linear(current_dim, current_dim // 2),
                nn.BatchNorm1d(current_dim // 2),
                activation_fn()
            ])
            current_dim = current_dim // 2
        layers.append(nn.Linear(current_dim, 1))
        return nn.Sequential(*layers)

    def initialize_weights(self):
        """Initialize weights using Kaiming initialization for layers with ReLU activation."""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                init.kaiming_normal_(m.weight, nonlinearity='relu')
                if m.bias is not None:
                    init.zeros_(m.bias)
            elif isinstance(m, nn.BatchNorm1d):
                init.ones_(m.weight)
                init.zeros_(m.bias)

    def forward(self, x):
        """Forward pass through the encoder."""
        encoded = self.encoder(x)
        return encoded

def train_model(model, epochs, relative_abundance, metadata, batch_size=64, lr_r=0.001, lr_g=0.001, lr_c=0.005):
    """Train the GAN model using K-Fold cross-validation."""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    all_eval_accuracies = []
    all_eval_aucs = []
    all_eval_f1s = []

    for fold, (train_index, val_index) in enumerate(skf.split(relative_abundance, metadata['disease'])):
        print(f"\nFold {fold + 1}/5")

        # Re-initialize the model and optimizer
        model = GAN(input_dim=model.input_dim, latent_dim=model.latent_dim,
                    activation_fn=model.activation_fn, num_layers=model.num_layers)
        model.initialize_weights()
        model.to(device)

        # Initialize optimizers and schedulers
        optimizer = optim.Adam(
            list(model.encoder.parameters()) + list(model.disease_classifier.parameters()), lr=lr_c
        )
        optimizer_distiller = optim.Adam(model.encoder.parameters(), lr=lr_g)
        optimizer_regression = optim.Adam(model.confounder_regressor.parameters(), lr=lr_r)
       

        scheduler = lr_scheduler.ReduceLROnPlateau(optimizer, mode='max', factor=0.5, patience=5)
        scheduler_distiller = lr_scheduler.ReduceLROnPlateau(optimizer_distiller, mode='min', factor=0.5, patience=5)
        scheduler_regression = lr_scheduler.ReduceLROnPlateau(optimizer_regression, mode='min', factor=0.5, patience=5)
       

        X_clr_df_train = relative_abundance.iloc[train_index].reset_index(drop=True)
        X_clr_df_val = relative_abundance.iloc[val_index].reset_index(drop=True)
        train_metadata = metadata.iloc[train_index].reset_index(drop=True)
        val_metadata = metadata.iloc[val_index].reset_index(drop=True)

        # best_loss = float('inf')
        best_disease_acc = 0
        early_stop_step = 20
        early_stop_patience = 0

        (training_feature_ctrl, metadata_ctrl_age, metadata_ctrl_bmi,
         training_feature_disease, metadata_disease_age, metadata_disease_bmi) = dcor_calculation_data(
            X_clr_df_train, train_metadata, device
        )

        # Lists to store losses
        r_losses, g_losses, c_losses = [], [], []
        dcs0_age, dcs1_age, mis0_age, mis1_age,  dcs0_bmi, dcs1_bmi, mis0_bmi, mis1_bmi = [], [], [], [], [], [], [], []
        train_disease_accs, val_disease_accs, train_disease_aucs, val_disease_aucs, train_disease_f1s, val_disease_f1s = [], [], [], [], [], []

        for epoch in range(epochs):
            # Create batches
            (training_feature_ctrl_batch, metadata_ctrl_batch_age, metadata_ctrl_batch_bmi,
             training_feature_batch, metadata_batch_disease) = create_batch(
                X_clr_df_train, train_metadata, batch_size, False, device
            )


            # ----------------------------
            # Train age regressor (r_loss)
            # ----------------------------
            optimizer_regression.zero_grad()
            for param in model.encoder.parameters():
                param.requires_grad = False

            encoded_features = model.encoder(training_feature_ctrl_batch)
            confounder_predictions = model.confounder_regressor(encoded_features)
            # Combine age and BMI into a single tensor
            confounder_targets = torch.stack([
                metadata_ctrl_batch_age,
                metadata_ctrl_batch_bmi
            ], dim=1)
            r_loss = model.confounder_regression_loss(confounder_predictions, confounder_targets)
            r_loss.backward()
            optimizer_regression.step()
            scheduler_regression.step(r_loss)

            for param in model.encoder.parameters():
                param.requires_grad = True

            # ----------------------------
            # Train distiller age (g_age_loss)
            # ----------------------------
            optimizer_distiller.zero_grad()
            for param in model.confounder_regressor.parameters():
                param.requires_grad = False

            encoder_features = model.encoder(training_feature_ctrl_batch)
            reversed_encoded = model.grl(encoder_features)
            confounder_predictions = model.confounder_regressor(reversed_encoded)
            g_loss = model.confounder_distiller_loss(confounder_predictions, confounder_targets)
            g_loss.backward()
            optimizer_distiller.step()
            scheduler_distiller.step(g_loss)

            for param in model.confounder_regressor.parameters():
                param.requires_grad = True
            

            # ----------------------------
            # Train encoder & classifier (c_loss)
            # ----------------------------
            optimizer.zero_grad()
            encoded_feature_batch = model.encoder(training_feature_batch)
            prediction_scores = model.disease_classifier(encoded_feature_batch).view(-1)
            c_loss = model.disease_classifier_loss(prediction_scores, metadata_batch_disease)
            c_loss.backward()
            pred_tag = (torch.sigmoid(prediction_scores) > 0.5).float()
            disease_acc = balanced_accuracy_score(metadata_batch_disease.cpu(), pred_tag.cpu())
            disease_auc = calculate_auc(metadata_batch_disease.cpu(), prediction_scores.cpu())
            disease_f1 = f1_score(metadata_batch_disease.cpu(), pred_tag.cpu())
            optimizer.step()
            scheduler.step(disease_acc)

            # Store the losses
            r_losses.append(r_loss.item())
            g_losses.append(g_loss.item())
            c_losses.append(c_loss.item())

            train_disease_accs.append(disease_acc)
            train_disease_aucs.append(disease_auc)
            train_disease_f1s.append(disease_f1)

            # Early stopping check (optional)
            if disease_acc > best_disease_acc:
                best_disease_acc = disease_acc
                early_stop_patience = 0
            else:
                early_stop_patience += 1
            # Uncomment the early stopping if needed
            if early_stop_patience == early_stop_step:
                print("Early stopping triggered.")
                break

            # Analyze distance correlation and learned features
            with torch.no_grad():
                feature0 = model.encoder(training_feature_ctrl)
                dc0_age = dcor.u_distance_correlation_sqr(feature0.cpu().numpy(), metadata_ctrl_age)
                mi_ctrl_age = mutual_info_regression(feature0.cpu().numpy(), metadata_ctrl_age)
                dc0_bmi = dcor.u_distance_correlation_sqr(feature0.cpu().numpy(), metadata_ctrl_bmi)
                mi_ctrl_bmi = mutual_info_regression(feature0.cpu().numpy(), metadata_ctrl_bmi)


                feature1 = model.encoder(training_feature_disease)
                dc1_age = dcor.u_distance_correlation_sqr(feature1.cpu().numpy(), metadata_disease_age)
                mi_disease_age = mutual_info_regression(feature1.cpu().numpy(), metadata_disease_age)
                dc1_bmi = dcor.u_distance_correlation_sqr(feature1.cpu().numpy(), metadata_disease_bmi)
                mi_disease_bmi = mutual_info_regression(feature1.cpu().numpy(), metadata_disease_bmi)

            dcs0_age.append(dc0_age)
            dcs1_age.append(dc1_age)
            mis0_age.append(mi_ctrl_age.mean())
            mis1_age.append(mi_disease_age.mean())

            dcs0_bmi.append(dc0_bmi)
            dcs1_bmi.append(dc1_bmi)
            mis0_bmi.append(mi_ctrl_bmi.mean())
            mis1_bmi.append(mi_disease_bmi.mean())

            print(f"Epoch {epoch + 1}/{epochs}, r_loss: {r_loss.item():.4f},"
                  f"g_loss: {g_loss.item():.4f} "
                  f" c_loss: {c_loss.item():.4f}, disease_acc: {disease_acc:.4f}")

            # Evaluate
            eval_accuracy, eval_auc, eval_f1 = evaluate(
                model, X_clr_df_val, val_metadata, val_metadata.shape[0], 'eval', device
            )
            # Optionally, evaluate on training data
            # _, _ = evaluate(model, X_clr_df_train, train_metadata, train_metadata.shape[0], 'train', device)

            val_disease_accs.append(eval_accuracy)
            val_disease_aucs.append(eval_auc)
            val_disease_f1s.append(eval_f1)

        # Save models
        torch.save(model.encoder.state_dict(), f'models/encoder_fold{fold}.pth')
        torch.save(model.disease_classifier.state_dict(), f'models/disease_classifier_fold{fold}.pth')
        print(f'Encoder saved to models/encoder_fold{fold}.pth.')
        print(f'Classifier saved to models/disease_classifier_fold{fold}.pth.')

        all_eval_accuracies.append(eval_accuracy)
        all_eval_aucs.append(eval_auc)
        all_eval_f1s.append(eval_f1)
        plot_losses(r_losses, g_losses, c_losses, dcs0_age, dcs1_age, mis0_age, mis1_age, dcs0_bmi, dcs1_bmi, mis0_bmi, mis1_bmi, train_disease_accs, val_disease_accs, train_disease_aucs, val_disease_aucs, train_disease_f1s, val_disease_f1s, fold)

    avg_eval_accuracy = np.mean(all_eval_accuracies)
    avg_eval_auc = np.mean(all_eval_aucs)
    save_eval_results(all_eval_accuracies, all_eval_aucs, all_eval_f1s)
    return avg_eval_accuracy, avg_eval_auc

def evaluate(model, relative_abundance, metadata, batch_size, t, device):
    """Evaluate the trained GAN model."""
    model.to(device)

    feature_batch, metadata_batch_disease = create_batch(
        relative_abundance, metadata, batch_size, is_test=True, device=device
    )
    with torch.no_grad():
        encoded_feature_batch = model.encoder(feature_batch)
        prediction_scores = model.disease_classifier(encoded_feature_batch).view(-1)

    pred_tag = (torch.sigmoid(prediction_scores) > 0.5).float().cpu()
    disease_acc = balanced_accuracy_score(metadata_batch_disease.cpu(), pred_tag.cpu())
    c_loss = model.disease_classifier_loss(prediction_scores.cpu(), metadata_batch_disease.cpu())

    auc = calculate_auc(metadata_batch_disease.cpu(), prediction_scores.cpu())
    f1 = f1_score(metadata_batch_disease.cpu(), pred_tag.cpu())

    print(f"{t} result --> Accuracy: {disease_acc:.4f}, Loss: {c_loss.item():.4f}, AUC: {auc}, F1: {f1:.4f}")
    return disease_acc, auc, f1

def calculate_auc(metadata_batch_disease, prediction_scores):
    """Calculate AUC."""
    if len(torch.unique(metadata_batch_disease)) > 1:
        auc = roc_auc_score(metadata_batch_disease.cpu(), torch.sigmoid(prediction_scores).detach().cpu())
        return auc
    else:
        print("Cannot compute ROC AUC as only one class is present.")
        return None

def plot_losses(r_losses, g_losses, c_losses, dcs0_age, dcs1_age, mis0_age, mis1_age, dcs0_bmi, dcs1_bmi, mis0_bmi, mis1_bmi, train_disease_accs, val_disease_accs, train_disease_aucs, val_disease_aucs, train_disease_f1s, val_disease_f1s, fold):
    """Plot training losses and save the figures."""
    plot_single_loss(c_losses, 'c_loss', 'blue', f'confounder_free_closs_fold{fold}.png')

    plot_single_loss(g_losses, 'g_loss', 'green', f'confounder_free_gloss_fold{fold}.png')
    plot_single_loss(r_losses, 'r_loss', 'red', f'confounder_free_rloss_fold{fold}.png')
    plot_single_loss(dcs0_age, 'dc0', 'orange', f'confounder_free_age_dc0_fold{fold}.png')
    plot_single_loss(dcs1_age, 'dc1', 'orange', f'confounder_free_age_dc1_fold{fold}.png')
    plot_single_loss(mis0_age, 'mi0', 'purple', f'confounder_free_age_mi0_fold{fold}.png')
    plot_single_loss(mis1_age, 'mi1', 'purple', f'confounder_free_age_mi1_fold{fold}.png')

    plot_single_loss(dcs0_bmi, 'dc0', 'orange', f'confounder_free_bmi_dc0_fold{fold}.png')
    plot_single_loss(dcs1_bmi, 'dc1', 'orange', f'confounder_free_bmi_dc1_fold{fold}.png')
    plot_single_loss(mis0_bmi, 'mi0', 'purple', f'confounder_free_bmi_mi0_fold{fold}.png')
    plot_single_loss(mis1_bmi, 'mi1', 'purple', f'confounder_free_bmi_mi1_fold{fold}.png')

    plot_single_loss(train_disease_accs, 'train_disease_acc', 'red', f'confounder_free_train_disease_acc_fold{fold}.png')
    plot_single_loss(train_disease_aucs, 'train_disease_auc', 'red', f'confounder_free_train_disease_auc_fold{fold}.png')
    plot_single_loss(train_disease_f1s, 'train_disease_f1', 'red', f'confounder_free_train_disease_f1_fold{fold}.png')
    plot_single_loss(val_disease_accs, 'val_disease_acc', 'red', f'confounder_free_val_disease_acc_fold{fold}.png')
    plot_single_loss(val_disease_aucs, 'val_disease_auc', 'red', f'confounder_free_val_disease_auc_fold{fold}.png')
    plot_single_loss(val_disease_f1s, 'val_disease_f1', 'red', f'confounder_free_val_disease_f1_fold{fold}.png')
    

def plot_single_loss(values, label, color, filename):
    """Helper function to plot a single loss."""
    plt.figure(figsize=(12, 6))
    plt.plot(values, label=label, color=color)
    plt.xlabel('Epoch')
    plt.ylabel(label)
    plt.title(f'{label} Over Epochs')
    plt.legend()
    plt.grid(True)
    plt.savefig(f'plots/{filename}')
    plt.close()

def save_eval_results(accuracies, aucs, f1s, filename='evaluation_results.json'):
    """Save evaluation accuracies and AUCs to a JSON file."""
    results = {'accuracies': accuracies, 'aucs': aucs, 'f1s': f1s}
    with open(filename, 'w') as f:
        json.dump(results, f, indent=4)
    print(f"Evaluation results saved to {filename}.")
