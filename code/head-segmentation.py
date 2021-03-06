# -*- coding: utf-8 -*-
__author__ = 'Zhenyuan Shen: https://kaggle.com/szywind'

from subprocess import check_output
print(check_output(["ls", "../input"]).decode("utf8"))

import numpy as np # linear algebra
import pandas as pd # data processing, CSV file I/O (e.g. pd.read_csv)
import os, time, gc, imutils, cv2
from keras.callbacks import ReduceLROnPlateau, EarlyStopping, ModelCheckpoint, TensorBoard
from keras.preprocessing.image import ImageDataGenerator
from tqdm import tqdm
from keras import optimizers
from keras.models import model_from_json

# from sklearn.cross_validation import KFold, StratifiedKFold
from sklearn.model_selection import KFold
from constants import *
from helpers import *
import math
import glob
import random
from PIL import Image
from sklearn.model_selection import train_test_split
import unet
import pspnet
import tiramisunet

np.set_printoptions(threshold='nan')

class HeadSeg():
    def __init__(self, train = True, input_width=512, input_height=512, batch_size=1, epochs=100, learn_rate=1e-2, nb_classes=2):
        self.input_width = input_width
        self.input_height = input_height
        self.batch_size = batch_size
        self.epochs = epochs
        self.learn_rate = learn_rate
        self.nb_classes = nb_classes

        if MODEL_TYPE == MODEL.UNET or MODEL_TYPE == MODEL.REFINED_UNET:
            self.model = unet.get_unet_512(input_shape=(self.input_height, self.input_width, 3))

        elif MODEL_TYPE == MODEL.TIRAMISUNET:
            self.model = tiramisunet.get_tiramisunet(input_shape=(self.input_height, self.input_width, 3))

        elif MODEL_TYPE == MODEL.PSPNET2:
            self.model = pspnet.pspnet2(input_shape=(self.input_height, self.input_width, 3))

        self.model.summary()
        if train:
            self.net_path = '../weights/model.json'
            self.model_path = '../weights/head-segmentation-model.h5'
            with open(self.net_path, 'w') as json_file:
                json_file.write(self.model.to_json())
        else:
            self.net_path = '../weights/{}/model.json'.format(MODEL_DIR)
            self.model_path = '../weights/{}/head-segmentation-model.h5'.format(MODEL_DIR)

        self.threshold = 0.5
        self.direct_result = True
        self.load_data()
        self.factor = 1
        self.train_with_all = False
        self.apply_crf = False

    # Load Data & Make Train/Validation Split
    def load_data(self):
        ids_train = []
        with open(INPUT_PATH + TRAIN_DATASET, 'r') as f:
            for line in f:
                ids_train.append(line.strip().split())
        self.ids_train_split, self.ids_valid_split = train_test_split(ids_train, test_size=0.15, random_state=42)


    def train(self):

        try:
            self.model.load_weights(self.model_path)
        except:
            pass
        nTrain = len(self.ids_train_split)
        nValid = len(self.ids_valid_split)
        print('Training on {} samples'.format(nTrain))
        print('Validating on {} samples'.format(nValid))

        ## Prepare Data
        def train_generator():
            while True:
                for start in range(0, nTrain, self.batch_size):
                    x_batch = []
                    y_batch = []
                    end = min(start + self.batch_size, nTrain)
                    ids_train_batch = self.ids_train_split[start:end]

                    for img_path, mask_path in ids_train_batch:
                        img = cv2.imread(img_path)
                        img = cv2.resize(img, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR)
                        mask = cv2.imread(mask_path)[...,0]
                        mask = cv2.resize(mask, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR)
                        img, mask = randomShiftScaleRotate(img, mask,
                                                           shift_limit=(-0.0625, 0.0625),
                                                           scale_limit=(-0.125, 0.125),
                                                           rotate_limit=(-0, 0))
                        img, mask = randomHorizontalFlip(img, mask)
                        img = randomGammaCorrection(img)
                        if self.factor != 1:
                            img = cv2.resize(img, (self.input_dim/self.factor, self.input_dim/self.factor), interpolation=cv2.INTER_LINEAR)
                        # draw(img, mask)

                        if self.direct_result:
                            mask = np.expand_dims(mask, axis=2)
                            x_batch.append(img)
                            y_batch.append(mask)
                        else:
                            target = np.zeros((mask.shape[0], mask.shape[1], self.nb_classes))
                            for k in range(self.nb_classes):
                                target[:,:,k] = (mask == k)
                            x_batch.append(img)
                            y_batch.append(target)

                    x_batch = np.array(x_batch, np.float32) / 255.0
                    y_batch = np.array(y_batch, np.float32)
                    if USE_REFINE_NET:
                        yield x_batch, [y_batch, y_batch]
                    else:
                        yield x_batch, y_batch

        def valid_generator():
            while True:
                for start in range(0, nValid, self.batch_size):
                    x_batch = []
                    y_batch = []
                    end = min(start + self.batch_size, nValid)
                    ids_valid_batch = self.ids_valid_split[start:end]
                    for img_path, mask_path in ids_valid_batch:
                        img = cv2.imread(img_path)
                        img = cv2.resize(img, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR)
                        mask = cv2.imread(mask_path)[...,0]
                        mask = cv2.resize(mask, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR)
                        if self.factor != 1:
                            img = cv2.resize(img, (self.input_dim/self.factor, self.input_dim/self.factor), interpolation=cv2.INTER_LINEAR)
                        if self.direct_result:
                            mask = np.expand_dims(mask, axis=2)
                            x_batch.append(img)
                            y_batch.append(mask)
                        else:
                            target = np.zeros((mask.shape[0], mask.shape[1], self.nb_classes))
                            for k in range(self.nb_classes):
                                target[:,:,k] = (mask == k)
                            x_batch.append(img)
                            y_batch.append(target)

                    x_batch = np.array(x_batch, np.float32) / 255.0
                    y_batch = np.array(y_batch, np.float32)
                    if USE_REFINE_NET:
                        yield x_batch, [y_batch, y_batch]
                    else:
                        yield x_batch, y_batch

        callbacks = [EarlyStopping(monitor='val_loss',
                                       patience=6,
                                       verbose=1,
                                       min_delta=1e-4),
                    ReduceLROnPlateau(monitor='val_loss',
                                           factor=0.1,
                                           patience=2,
                                           cooldown=2,
                                           verbose=1),
                    ModelCheckpoint(filepath=self.model_path,
                                         save_best_only=True,
                                         save_weights_only=True),
                    TensorBoard(log_dir='logs')]

        # Set Training Options
        # opt = optimizers.RMSprop(lr=0.0001)
        opt = optimizers.RMSpropAccum(lr=1e-4, accumulator=16)

        if USE_REFINE_NET:
            self.model.compile(optimizer=opt,
                               loss=bce_dice_loss,
                               loss_weights=[1, 1],
                               metrics=[dice_score]
                               )
        else:
            self.model.compile(optimizer=opt,
                               loss=bce_dice_loss,
                               metrics=[dice_score]
                               )

        self.model.fit_generator(
            generator=train_generator(),
            steps_per_epoch=math.ceil(nTrain / float(self.batch_size)),
            epochs=1,
            verbose=1,
            callbacks=callbacks,
            validation_data=valid_generator(),
            validation_steps=math.ceil(nValid / float(self.batch_size)))

        self.model.fit_generator(
            generator=train_generator(),
            steps_per_epoch=math.ceil(nTrain / float(self.batch_size)),
            epochs=self.epochs,
            verbose=2,
            callbacks=callbacks,
            validation_data=valid_generator(),
            validation_steps=math.ceil(nValid / float(self.batch_size)))

    def test_one(self, list_file='lfw-deepfunneled.txt'):
        if not os.path.isfile(self.net_path) or not os.path.isfile(self.model_path):
            raise RuntimeError("No model found.")

        json_file = open(self.net_path, 'r')
        loaded_model_json = json_file.read()
        self.model = model_from_json(loaded_model_json)
        self.model.load_weights(self.model_path)

        ids_test = []

        with open(INPUT_PATH + list_file, 'r') as f:
            for line in f:
                ids_test.append(line.strip().split())

        nTest = len(ids_test)
        print('Testing on {} samples'.format(nTest))

        str = []
        nbatch = 0

        IoU = 0
        for start in range(0, nTest, self.batch_size):
            print(nbatch)
            nbatch += 1
            x_batch = []
            y_batch = []
            images = []
            end = min(start + self.batch_size, nTest)
            ids_test_batch = ids_test[start:end]
            for image_path, mask_path in ids_test_batch:
                print(image_path)
                print(mask_path)
                raw_img = cv2.imread(image_path)
                img = cv2.resize(raw_img, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR)
                x_batch.append(img)
                images.append(raw_img)

                mask = cv2.imread(mask_path)[..., 0]
                y_batch.append(mask)

            x_batch = np.array(x_batch, np.float32) / 255.0

            p_test = self.model.predict(x_batch, batch_size=self.batch_size)

            if USE_REFINE_NET:
                p_test = p_test[-1]

            if self.direct_result:
                result, probs = get_final_mask(p_test, self.threshold, apply_crf=self.apply_crf, images=images)
            else:
                avg_p_test = p_test[...,1] - p_test[...,0]
                result = get_result(avg_p_test, 0)

            for i in range(len(y_batch)):
                IoU += numpy_dice_score(y_batch[i], result[i]) / nTest

            str.extend(map(run_length_encode, result))

            # save predicted masks
            if not os.path.exists(OUTPUT_PATH):
                os.mkdir(OUTPUT_PATH)

            for i in range(start, end):
                image_path, mask_path = ids_test[i]
                img_path = image_path[image_path.rfind('/')+1:]
                res_mask = (255 * result[i-start]).astype(np.uint8)
                res_mask = np.dstack((res_mask,)*3)
                cv2.imwrite(OUTPUT_PATH + '{}'.format(img_path), res_mask)

        print('mean IoU: {}'.format(IoU))


if __name__ == "__main__":
    ccs = HeadSeg(input_width=INPUT_WIDTH, input_height=INPUT_HEIGHT, train=IS_TRAIN, nb_classes=NUM_CLASS)
    if IS_TRAIN:
        ccs.train()
    ccs.test_one(list_file=TEST_DATASET)