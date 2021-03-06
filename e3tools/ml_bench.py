import re
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt

from sklearn.preprocessing import LabelEncoder, OrdinalEncoder, OneHotEncoder
from sklearn.preprocessing import StandardScaler, MinMaxScaler, PowerTransformer, QuantileTransformer
from sklearn.impute import SimpleImputer

from sklearn.feature_selection import VarianceThreshold, SelectKBest
from sklearn.feature_selection import f_regression, chi2, f_classif

from sklearn.model_selection import cross_val_score, cross_val_predict, learning_curve, train_test_split
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV
from sklearn.calibration import CalibratedClassifierCV
from sklearn.utils.fixes import loguniform

from sklearn.metrics import roc_auc_score, accuracy_score, confusion_matrix, log_loss, plot_roc_curve
from sklearn.metrics import mean_squared_error, mean_absolute_error, explained_variance_score, classification_report
from sklearn.inspection import plot_partial_dependence, permutation_importance

import e3tools.eda_table as et
import e3tools.eda_display_utils as edu

from scipy.stats import spearmanr
from scipy.cluster import hierarchy
from collections import OrderedDict, Counter
from copy import deepcopy

import collections
import pydotplus
from sklearn import metrics, tree
from IPython.display import Image, display

try:
    import cPickle as pickle
except BaseException:
    import pickle

def split_feature_labels(tbl, c_label):
    y = tbl[c_label].copy()
    X = tbl.drop([c_label], axis=1).copy()
    return (X, y)


def merge_feature_labels(X, y, c_label):
    X[c_label] = y
    return X


def apply_transform(transformer, vals):
    # print(transformer, vals[:10])
    # import pdb; pdb.set_trace()
    # return transformer.fit_transform(vals.reshape(-1, 1)).flatten()
    return transformer.fit_transform(vals.reshape(-1, 1)).flatten()


def print_decision_tree(clf, feature_names, class_names):
    sns.set(color_codes=True)

    dot_data = tree.export_graphviz(clf, out_file=None,
                             feature_names=feature_names, filled=True, rounded=True,
                             class_names=class_names)
    graph = pydotplus.graph_from_dot_data(dot_data)
    colors = ('grey', 'yellow')
    edges = collections.defaultdict(list)

    for edge in graph.get_edge_list():
        edges[edge.get_source()].append(int(edge.get_destination()))

    for edge in edges:
        edges[edge].sort()    
        for i in range(2):
            dest = graph.get_node(str(edges[edge][i]))[0]
            dest.set_fillcolor(colors[i])
    display(Image(graph.create_png()))


def print_decision_tree_new(clf, x_data, y_data, feature_names, class_names):
    viz = dtreeviz(clf, 
                   x_data=x_data,
                   y_data=y_data,
                   target_name='class',
                   feature_names=feature_names, 
                   class_names=class_names, 
                   title="Decision Tree")
    display(viz)


class MLTable(et.EDATable):
    """This class prepare data for supervised learning
    - Note that the data type for c_label should be binary
    """
    def __init__(self, tbl_d, c_label=None, tbl_h=None, name='Default', dtypes={}, label_values=None):
        """ Initialize table for ML
            - tbl_h: optional held-out DataFrame
        """
        self.name = name
        self.c_label = c_label
        self.cs_special = [e for e in ["rowtype", self.c_label] if e is not None]
        self.label_values = label_values
        tbl_d["rowtype"] = "dev"
        if tbl_h is None:
            tbl = tbl_d
        else:
            # import pdb; pdb.set_trace()
            if c_label not in tbl_h.columns:
                print("Adding a dummy label column")
                tbl_h[c_label] = None
            tbl_h["rowtype"] = "heldout"
            tbl = pd.concat([tbl_d, tbl_h], axis=0, sort=False)
            tbl.reset_index(inplace=True, drop=True)
        super(MLTable, self).__init__(tbl, dtypes)
        if c_label:
            if self.dtypes[c_label] == "object":
                if self.vcounts[c_label] == 2:
                    self.task_type = "classification-binary"
                elif self.vcounts[c_label] > 2:
                    self.task_type = "classification-multiclass"
                else:
                    print("At least two unique values are required!")
            else:
                self.task_type = "regression"
        else:
            self.task_type = "unsupervised"

        print("Task type: " + self.task_type)

    def get_info(self):
        return {
            "tbl_name":self.name,
            "raw_features":len(self.tbl.columns),
            "encoded_features":len(self.tbl_e.columns),
            "train_set":len(self.train),
            "test_set":len(self.test),
            }

    def preprocess(self, topk_filters={'all':3}, scalers={'all':StandardScaler()}, imputers={'all':SimpleImputer()}):
        """ Preprocess column values by data type
        Categorical / String:
        - Filter out minority values
        Numerical:
        - Standardize range
        - Impute missing values
        """
        res = []
        for c in self.cols:
            if c in self.cs_special:
                res.append(self.tbl[c])                
            elif self.dtypes[c] in ('category', 'object'):
                if c in topk_filters:
                    topk = topk_filters[c]
                elif 'all' in topk_filters:
                    topk = topk_filters['all']
                else:
                    topk = 3
                if self.vcounts[c] > topk:
                    topk_vals = self.get_topk_vals(c, topk)
                    res.append(self.tbl[c].apply(lambda e: e if e in topk_vals else np.nan))
                else:
                    res.append(self.tbl[c])
            else:
                # Scaling
                if c in scalers:
                    v_scaled = apply_transform(scalers[c], self.tbl[c].values)
                elif 'all' in scalers and scalers['all']:
                    v_scaled = apply_transform(scalers['all'], self.tbl[c].values)
                else:
                    v_scaled = self.tbl[c].values
                # Imputation
                if c in imputers:
                    v_imputed = apply_transform(imputers[c], v_scaled)
                elif 'all' in imputers:
                    v_imputed = apply_transform(imputers['all'], v_scaled)
                else:
                    v_imputed = v_scaled
                res.append(pd.Series(v_imputed, name=c))
        self.tbl_n = pd.concat(res, axis=1)
        return self.tbl_n

    def encode(self, cols=None, col_ptn=None, impute=None, omit_constant=True):
        """ Encode column values as features
        """
        if col_ptn:
            cols = [c for c in self.cols if re.search(col_ptn, c)]
        elif not cols:
            cols = self.cols
        if hasattr(self, "tbl_n"):
            print("Using normalized table...")
            tbl = self.tbl_n
        else:
            tbl = self.tbl
        res = []
        for c in self.cs_special:
            # if c == self.c_label and task_type == 'classification-multiclass':
            #     le = preprocessing.LabelEncoder()
            #     self.label_values
            # else:
            res.append(tbl[c])
        for c in cols:
            if self.vcounts[c] <= 1 and omit_constant:
                print("Omitting constant: %s" % c)
                continue
            if c in self.cs_special:
                continue
            elif self.dtypes[c] == 'category':
                categories = tbl[c].cat.categories
                res.append(tbl[c].astype(str).replace(categories, list(range(len(categories)))).astype(str))
            elif self.dtypes[c] == 'object':
                res.append(pd.get_dummies(tbl[c], prefix=c))
            else:
                res.append(tbl[c])
        self.tbl_e = pd.concat(res, axis=1)
        # self.c_features = self.get_feature_list()
        print('Encoded DF Shape:', self.tbl_e.shape)
        return self.tbl_e

    def fselect(self, topk=None):
        """ Feature selection
        """
        t_special = self.tbl_e[self.tbl_e.rowtype=="dev"][self.cs_special]
        t_features = self.tbl_e[self.tbl_e.rowtype=="dev"].drop(self.cs_special, axis=1)
        if topk:
            if "classification" in self.task_type:
                kfs = SelectKBest(k=topk, score_func=f_classif)
            elif "regression":
                kfs = SelectKBest(k=topk, score_func=f_regression)
            else:
                print("Invalid task_type: %s" % self.task_type)
            a_selected = kfs.fit_transform(t_features, t_special[self.c_label])
            cs_selected = t_features.columns[kfs.get_support()]
            self.tbl_e = self.tbl_e[self.cs_special+cs_selected.tolist()]
        print('Encoded DF Shape:', self.tbl_e.shape)

    def split(self, test_size=0.2, random_state=None, silent=False, verbose=False):
        """ Split dataset for train/test
        """
        if not hasattr(self, "tbl_e"):
            print("Encode columns as features first!")
            return
        self.dev = self.tbl_e[self.tbl_e.rowtype=="dev"].drop("rowtype", axis=1)
        self.heldout = self.tbl_e[self.tbl_e.rowtype=="heldout"].drop(["rowtype"], axis=1) #, self.c_label
        if len(self.heldout) > 0 and not silent:
            print('Heldout Shape:', self.heldout.shape)

        self.train, self.test = train_test_split(self.dev, test_size=test_size, random_state=random_state)
        if not silent:
            print('Dev Shape:', self.dev.shape)
            print('Train Shape:', self.train.shape)
            print('Test Shape:', self.test.shape)

        if verbose:
            self.colinfo()
            EDATable(self.tbl_n).colinfo()
            EDATable(self.tbl_e).colinfo()

    def fcorr(self):
        """ Visualize feature correlation
        """
        X, _ = split_feature_labels(self.dev, self.c_label)
        display(X.corr(method="spearman").round(3).style.background_gradient(cmap='cool'))

    def fcluster(self):
        """ Visualize feature clustering results
        """
        X, _ = split_feature_labels(self.dev, self.c_label)

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 8))
        corr = spearmanr(X).correlation
        corr_linkage = hierarchy.ward(corr)
        dendro = hierarchy.dendrogram(corr_linkage, labels=self.dev.columns, ax=ax1,
                                      leaf_rotation=90)
        dendro_idx = np.arange(0, len(dendro['ivl']))

        ax2.imshow(corr[dendro['leaves'], :][:, dendro['leaves']])
        ax2.set_xticks(dendro_idx)
        ax2.set_yticks(dendro_idx)
        ax2.set_xticklabels(dendro['ivl'], rotation='vertical')
        ax2.set_yticklabels(dendro['ivl'])
        fig.tight_layout()
        plt.show()

    def balance_train(self, random_state=None):
        """ Balance Labels
        """
        if not hasattr(self, "train"):
            print("Split data first...")
            return

        rus = RandomUnderSampler(random_state=random_state)
        self.train = merge_feature_labels(*rus.fit_resample(*split_feature_labels(self.train, self.c_label)), self.c_label)
        print('Train Shape:', self.train.shape)
        return self.train


class MLModel:
    """This class abstract various ML models
    """
    def __init__(self, name, model=None, model_filename=None, param_dist={}):
        self.name = name
        if model is not None:
            self.model = model
        elif model_filename:
            with open('%s.pkl' % model_filename, 'rb') as fin:
                self.model = pickle.load(fin)
        self.param_dist = param_dist

    def train(self, X, y):
        self.model.fit(X, y)

    def get_ypred_from_score(self, ypred_score):
        return [self.model.classes_[i] for i in np.argmax(ypred_score, 1)]

    def inspect(self):
        """Inspect model internals
        """
        pass

    def optimize(self, X, y, n_iter_search=1, scoring=None):
        """Find optimal hyperparameters
        """
        self.model = RandomizedSearchCV(
            estimator=self.model, 
            param_distributions=self.param_dist,
            n_iter=n_iter_search,
            scoring=scoring)
        self.model.fit(X, y)
        print("%s:" % self.name, self.model.best_params_)


    def predict(self, tbl_X):
        res = tbl_X
        res['Prediction'] = self.model.predict(tbl_X.values)
        return res


    def evaluate(self, X, y, task_type, verbose=False, ax=None, ypred=None, ypred_score=None, ypred_score_thr=None, label_values=None):
        """
        Calculates metrics and display it
        """
        # Getting the predicted values
        if ypred is None:
            ypred = self.model.predict(X)
        
        # Calculating metrics
        if task_type == "classification-binary":
            if ypred_score is None:
                ypred_score = self.model.predict_proba(X)
            if ypred_score_thr:
                print("ypred_score_thr: %s" % ypred_score_thr)
                ypred = np.where(ypred_score[:, 1] > ypred_score_thr, True, False)
                
            accuracy = accuracy_score(y, ypred)
            roc_auc = roc_auc_score(y, pd.DataFrame(ypred_score)[1])
            confusion = confusion_matrix(y, ypred)
            logloss = log_loss(y, ypred_score)
            
            type1_error = confusion[0][1] / confusion[0].sum() # False Positive
            type2_error = confusion[1][0] / confusion[1].sum() # False Negative
            
            # Score threshold by output
            # pred_score = pd.DataFrame({'pred':ypred, 'score':ypred_score[:, 1]})
            # et.EDATable(pred_score).desc_group('pred', output='desc')
            # display(pred_score.pred.value_counts())

            if verbose:
                # import pdb; pdb.set_trace()
                print('Confusion Matrix: \n', confusion)
                sns.histplot(ypred_score[:, 1], kde=False, bins=50, ax=ax)
                ax2 = ax.twinx()
                plot_roc_curve(self.model, X, y, ax=ax2)

            return {
                "model_name":self.name,
                "accuracy":accuracy, 
                "roc_auc":roc_auc,
                "log_loss":logloss, 
                "type1_error":type1_error,
                "type2_error":type2_error
            }
        elif task_type == "classification-multiclass":
            ypred_score = self.model.predict_proba(X)
            accuracy = accuracy_score(y, ypred)
            roc_auc = roc_auc_score(y, ypred_score, multi_class="ovr")
            confusion = confusion_matrix(y, ypred, labels=label_values)
            logloss = log_loss(y, ypred_score)
            # display(ypred_score)
            # import pdb; pdb.set_trace()
            
            if verbose:
                # display(ypred)
                print(classification_report(y, ypred, labels=label_values))
                print('Confusion Matrix:')
                display(pd.DataFrame(confusion, index=label_values, columns=label_values))

            return {
                "model_name":self.name,
                "accuracy":accuracy, 
                "log_loss":logloss, 
                "roc_auc":roc_auc,
                }            
        else:
            mse = mean_squared_error(y, ypred)
            mae = mean_absolute_error(y, ypred)
            evs = explained_variance_score(y, ypred)

            return {
                "model_name":self.name,
                "mean_squared_error":mse, 
                "mean_absolute_error":mae,
                "explained_variance_score":evs,
                }

    def export_to_file(self, filename):
        with open('%s.pkl' % filename, 'wb') as fout:
            pickle.dump(self.model, fout)

class MLBench:
    """This class performs supervised learning for given set of data
    """
    def __init__(self):
        self.tables = OrderedDict()
        self.models = OrderedDict()
        self.fit_models = OrderedDict()

    def add_table(self, tbl):
        self.tables[tbl.name] = tbl

    def add_model(self, mdl):
        self.models[mdl.name] = mdl

    def train_batch(self):
        for tn, tbl in self.tables.items():
            for mn, mdl in self.models.items():
                self.fit_models[(tn, mn)] = deepcopy(mdl)
                self.fit_models[(tn, mn)].train(*split_feature_labels(tbl.train, tbl.c_label))

    def optimize_batch(self, n_iter_search=1, scoring=None):
        for tn, tbl in self.tables.items():
            for mn, mdl in self.models.items():
                self.fit_models[(tn, mn)] = deepcopy(mdl)
                self.fit_models[(tn, mn)].optimize(*split_feature_labels(tbl.train, tbl.c_label), n_iter_search, scoring)

    def cross_validate_batch(self, scoring='roc_auc', nfolds=5):
        res = []
        for tn, tbl in self.tables.items():
            for mn, mdl in self.models.items():
                eval_res = tbl.get_info()
                cv_score = cross_val_score(mdl.model, *split_feature_labels(tbl.train, tbl.c_label), scoring=scoring, cv=nfolds, n_jobs=-1)
                eval_res.update({"model_name":mn, ("cv_"+scoring):cv_score.mean()})
                res.append(eval_res)
        return pd.DataFrame(res).drop(['train_set', 'test_set'], axis=1)

    def evaluate_batch(self, dataset='validation', verbose=False, figsize=(6,6), **kwargs):
        res = []

        for tn, tbl in self.tables.items():
            if dataset=='validation':
                tbl_eval = tbl.test
            elif dataset=='heldout':
                tbl_eval = tbl.heldout
            if verbose and tbl.task_type == "classification-binary":
                fig, axes = plt.subplots(1, len(self.models), figsize=(figsize[0]*len(self.models), figsize[1]), sharey=False)
            else:
                axes = [None] * len(self.models)
            for j, (mn, mdl)  in enumerate(self.models.items()):
                eval_res = tbl.get_info()
                eval_res.update(
                    self.fit_models[(tn, mn)].evaluate(*split_feature_labels(tbl_eval, tbl.c_label), tbl.task_type, verbose=verbose, ax=axes[j], **kwargs)
                )
                res.append(eval_res)
        return pd.DataFrame(res)

    def export_batch(self):
        for tn, tbl in self.tables.items():
            for mn, mdl in self.models.items():
                self.fit_models[(tn, mn)].export_to_file("%s_%s" % (tn, mn))        

    def evaluate_ensemble(self, model_weights={}):
        """ Build an ensemble of existing smodels
        """
        e_model = None
        e_table = None
        e_ypred = None
        e_ypred_score = None
        for tn, tbl in self.tables.items():
            for j, (mn, mdl)  in enumerate(self.models.items()):
                ypred_score = self.fit_models[(tn, mn)].model.predict_proba(split_feature_labels(tbl.test, tbl.c_label)[0])
                if e_ypred_score is None:
                    e_table = tbl
                    e_model = deepcopy(self.fit_models[(tn, mn)])
                    e_ypred_score = ypred_score
                else:
                    e_ypred_score += ypred_score * model_weights.get((tn, mn), 0)

        ypred = e_model.get_ypred_from_score(e_ypred_score)
        return e_model.evaluate(*split_feature_labels(tbl.test, tbl.c_label), e_table.task_type, ypred=ypred, ypred_score=e_ypred_score)

    def plot_partial_dependence(self, feature_set=None):
        for tn, tbl in self.tables.items():
            X = split_feature_labels(tbl.train, tbl.c_label)[0]
            if not feature_set:
                feature_range = range(len(X.columns))
            else:
                feature_range = [i for i,e in enumerate(X.columns) if e in feature_set]
            for i in feature_range:
                fig, axes = plt.subplots(1, len(self.models), figsize=(5*len(self.models), 3), sharey=True)
                for j, (mn, mdl) in enumerate(self.models.items()):
                    axes[j].set_title("PDP for %s \non %s" % (mdl.name, tbl.name))
                    plot_partial_dependence(self.fit_models[(tn, mn)].model, X , [i], ax=axes[j])

    def plot_feature_importance(self, scoring=None, random_state=None, figsize=(6,6)):
        for tn, tbl in self.tables.items():
            fig, axes = plt.subplots(1, len(self.models), figsize=(figsize[0]*len(self.models), figsize[1]), sharey=False)
            for j, (mn, mdl) in enumerate(self.models.items()):
                if len(self.models.items()) == 1:
                    ax = axes
                else:
                    ax = axes[j]
                ax.set_title("Feature Importance for %s \non %s" % (mdl.name, tbl.name))
                X_train, y_train = split_feature_labels(tbl.train, tbl.c_label)
                result = permutation_importance(self.fit_models[(tn, mn)].model, X_train, y_train, scoring=scoring, random_state=random_state)
                sorted_idx = result.importances_mean.argsort()
                ax.boxplot(result.importances[sorted_idx].T,
                           vert=False, labels=X_train.columns[sorted_idx])

    def plot_learning_curve(self, scoring=None, random_state=None):
        for tn, tbl in self.tables.items():
            fig, axes = plt.subplots(1, len(self.models), figsize=(5*len(self.models), 4), sharey=False)
            for j, (mn, mdl) in enumerate(self.models.items()):
                if len(self.models.items()) == 1:
                    ax = axes
                else:
                    ax = axes[j]
                ax.set_title("Learning Curve for %s \non %s" % (mdl.name, tbl.name))
                train_sizes = [int(float(len(tbl.train)) * i/10) for i  in range(1, 9)]
                _, train_scores, test_scores = \
                    learning_curve(mdl.model, *split_feature_labels(tbl.train, tbl.c_label), 
                        train_sizes=train_sizes, scoring=scoring, random_state=random_state)
                train_scores_mean = np.mean(train_scores, axis=1)
                test_scores_mean = np.mean(test_scores, axis=1)
                ax.plot(train_sizes, train_scores_mean, 'o-', color="r",
                             label="Training score")
                ax.plot(train_sizes, test_scores_mean, 'o-', color="g",
                             label="Cross-validation score")
                ax.legend(loc="best")
