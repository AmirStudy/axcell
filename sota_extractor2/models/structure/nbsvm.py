import re
import string
from fastai.text import *  # just for utilty functions pd, np, Path etc.

from sklearn.linear_model import LogisticRegression
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

from ...helpers.training import set_seed

def transform_df(df):
    df=df.replace(re.compile(r"(xxref|xxanchor)-[\w\d-]*"), "\\1 ")
    df=df.replace(re.compile(r"(^|[ ])\d+\.\d+\b"), " xxnum ")
    df=df.replace(re.compile(r"(^|[ ])\d\b"), " xxnum ")
    df=df.replace(re.compile(r"\bdata set\b"), " dataset ")
    df = df.drop_duplicates(["text", "cell_content", "cell_type"]).fillna("")
    return df

def train_valid_split(df, seed=42, by="cell_content"):
    set_seed(seed, "val_split")
    contents = np.random.permutation(df[by].unique())
    val_split = int(len(contents)*0.1)
    val_keys = contents[:val_split]
    split = df[by].isin(val_keys)
    valid_df = df[split]
    train_df = df[~split]
    len(train_df), len(valid_df)
    return train_df, valid_df

def get_class_column(y, classIdx):
    if len(y.shape) == 1:
        return y == classIdx
    else:
        return y.iloc[:, classIdx]

def get_number_of_classes(y):
    if len(y.shape) == 1:
        return len(np.unique(y))
    else:
        return y.shape[1]

class NBSVM:
    def __init__(self, solver='liblinear', dual=True, C=4, ngram_range=(1, 2)):
        self.solver = solver  # 'lbfgs' - large, liblinear for small datasets
        self.dual = dual
        self.C = C
        self.ngram_range = ngram_range

    re_tok = re.compile(f'([{string.punctuation}“”¨«»®´·º½¾¿¡§£₤‘’])')
    
    def tokenize(self, s): 
        return self.re_tok.sub(r' \1 ', s).split()
        
    def pr(self, y_i, y):
        p = self.trn_term_doc[y == y_i].sum(0)
        return (p+1) / ((y == y_i).sum()+1)

    def get_mdl(self, y):
        y = y.values
        r = np.log(self.pr(1, y) / self.pr(0, y))
        m = LogisticRegression(C=self.C, dual=self.dual, solver=self.solver, max_iter=1000)
        x_nb = self.trn_term_doc.multiply(r)
        return m.fit(x_nb, y), r

    def bow(self, X_train):
        self.n = X_train.shape[0]
        self.vec = TfidfVectorizer(ngram_range=self.ngram_range, tokenizer=self.tokenize,
                                min_df=3, max_df=0.9, strip_accents='unicode', use_idf=1,
                                smooth_idf=1, sublinear_tf=1)
        return self.vec.fit_transform(X_train)

    def train_models(self, y_train):
        self.models = []
        for i in range(0, self.c):
            print('fit', i)
            m, r = self.get_mdl(get_class_column(y_train, i))
            self.models.append((m, r))

    def fit(self, X_train, y_train):
        self.trn_term_doc = self.bow(X_train)
        self.c = get_number_of_classes(y_train)
        self.train_models(y_train)

    def predict_proba(self, X_test):
        preds = np.zeros((len(X_test), self.c))
        test_term_doc = self.vec.transform(X_test)
        for i in range(0, self.c):
            m, r = self.models[i]
            preds[:, i] = m.predict_proba(test_term_doc.multiply(r))[:, 1]
        return preds
    
    def validate(self, X_test, y_test):
        acc = (np.argmax(self.predict_proba(X_test),  axis=1) == y_test).mean()
        return acc

def metrics(preds, true_y):
    y = true_y
    p = preds
    acc = (p == y).mean()
    tp = ((y != 0) & (p == y)).sum()
    fp = ((p != 0) & (p != y)).sum()
    prec = tp / (fp + tp)
    return {
        "precision": prec,
        "accuracy": acc,
        "TP": tp,
        "FP": fp,
    }


def preds_for_cell_content(test_df, probs, group_by=["cell_content"]):
    test_df = test_df.copy()
    test_df["pred"] = np.argmax(probs, axis=1)
    grouped_preds = test_df.groupby(group_by)["pred"].agg(
        lambda x: x.value_counts().index[0])
    grouped_counts = test_df.groupby(group_by)["pred"].count()
    results = pd.DataFrame({'true': test_df.groupby(group_by)["label"].agg(lambda x: x.value_counts().index[0]),
                            'pred': grouped_preds,
                            'counts': grouped_counts})
    return results

def preds_for_cell_content_multi(test_df, probs, group_by=["cell_content"]):
    test_df = test_df.copy()
    probs_df = pd.DataFrame(probs, index=test_df.index)
    test_df = pd.concat([test_df, probs_df], axis=1)
    grouped_preds = np.argmax(test_df.groupby(
        group_by)[probs_df.columns].sum().values, axis=1)
    grouped_counts = test_df.groupby(group_by)["label"].count()
    results = pd.DataFrame({'true': test_df.groupby(group_by)["label"].agg(lambda x: x.value_counts().index[0]),
                            'pred': grouped_preds,
                            'counts': grouped_counts})
    return results

def test_model(model, tdf):
    probs = model(tdf["text"])
    preds = np.argmax(probs, axis=1)
    print("Results of categorisation on text fagment level")
    print(metrics(preds, tdf.label))

    print("Results per cell_content grouped using majority voting")
    results = preds_for_cell_content(tdf, probs)
    print(metrics(results["pred"], results["true"]))

    print("Results per cell_content grouped with multi category mean")
    results = preds_for_cell_content_multi(tdf, probs)
    print(metrics(results["pred"], results["true"]))

    print("Results per cell_content grouped with multi category mean - only on fragments from the same paper that the coresponding table")
    results = preds_for_cell_content_multi(
        tdf[tdf.this_paper], probs[tdf.this_paper])
    print(metrics(results["pred"], results["true"]))
