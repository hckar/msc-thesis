# -*- coding: utf-8 -*-
"""
Created on Sun Feb 17 01:28:21 2019

@author: Cagri

VGG16 based canonical orientation detection classifier. Uses Places365 dataset
wieghts. Rotated images and labelw are crated on the fly. Must be used with
task specific rotnet.py file.
"""

import math, csv, os, itertools, cv2, keras, urllib.request
import matplotlib.pyplot as plt
import numpy as np, pickle as pk, datetime as dt
from rotnet import RotNetGen
from LR_Adam import Adam
from focal_loss import categorical_focal_loss
#from keras_LRFinder import LRFinder
from itertools import cycle
from scipy import interp
from sklearn.metrics import confusion_matrix, roc_curve, auc
from sklearn.preprocessing import label_binarize
from keras.preprocessing.image import ImageDataGenerator
from keras.applications.vgg16 import VGG16, preprocess_input
from keras.applications.resnet50 import ResNet50, preprocess_input
from keras.callbacks import ModelCheckpoint, EarlyStopping, TensorBoard, CSVLogger
from keras import Model, models, layers, optimizers

#%% Additional functions-------------------------------------------------------
# Save numerical outputs to a pickle file
def pickleSave(dictionary):
    with open(os.path.join(save_dir, 'out.pickle'), 'ab') as file_pi:
        pk.dump(dictionary, file_pi)
    file_pi.close()


# Write events to a csv file
def statsWrite(text):
    stats_csv = os.path.join(save_dir, 'orienter_stats.csv')
    if os.path.exists(stats_csv): mode = 'a'
    else: mode = 'w'
    split_text = text.split()
    with open(stats_csv, mode=mode, newline='') as stats:
        csv_writer = csv.writer(stats, delimiter=' ', quotechar='|', quoting=csv.QUOTE_MINIMAL)
        csv_writer.writerow(split_text)
    stats.close()


# Create the fine tuned model
def createModel():
    # Create a sequential model
    x = layers.Dropout(0.15)(vgg_conv.layers[14].output)    
    x = vgg_conv.layers[15](x)
    x = layers.BatchNormalization(momentum=0.99, epsilon=0.001)(x)
    x = vgg_conv.layers[16](x)
    x = layers.BatchNormalization(momentum=0.99, epsilon=0.001)(x)
    x = vgg_conv.layers[17](x)
    x = layers.BatchNormalization(momentum=0.99, epsilon=0.001)(x)
    x = vgg_conv.layers[18](x)
    x = layers.Dropout(0.15)(x)
    x = layers.Flatten()(x)
    x = layers.Dense(512, activation='relu')(x)
    x = layers.Dropout(0.5)(x)
    x = layers.Dense(5, activation='softmax', name='pred_layer')(x)
    model = Model(vgg_conv.input, x)
    for layer in model.layers: print(layer.name, layer.trainable) 
    model.summary()
    return model
  

#%% Plot functions-------------------------------------------------------------   
# History plot of the CNN model
def historyPlots(history):
    acc = history.history['acc']
    val_acc = history.history['val_acc']
    loss = history.history['loss']
    val_loss = history.history['val_loss']
    epochs = range(len(acc))
    xint = range(min(epochs), math.ceil(max(epochs))+1)
    plt.plot(epochs, acc, 'b', label='Training acc')
    plt.plot(epochs, val_acc, 'r', label='Validation acc')
    plt.title('Train and validation accuracy')
    plt.xlabel('Epochs'); plt.ylabel('Accuracy');
    plt.grid(color='k', linestyle='--', linewidth=1)
    plt.xticks(xint); plt.legend(); fig1 = plt.gcf()
    plt.tight_layout(); plt.show()
    fig1.savefig(os.path.join(save_dir, 'train-val_acc.png'), dpi=100)
    plt.plot(epochs, loss, 'b', label='Training loss')
    plt.plot(epochs, val_loss, 'r', label='Validation loss')
    plt.title('Train and validation loss')
    plt.legend();plt.xlabel('Epochs'); plt.ylabel('Loss');
    plt.grid(color='k', linestyle='--', linewidth=1)
    plt.xticks(xint);plt.legend(); fig2 = plt.gcf()
    plt.tight_layout(); plt.show()
    fig2.savefig(os.path.join(save_dir, 'train-val_loss.png'), dpi=100)


# Confusion matrix
def confMatrix(true_labels, pred_labels, classes, check, normalize=False):
    cmap = plt.cm.Blues
    cm = confusion_matrix(true_labels, pred_labels)
    if normalize: cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]     
    plt.imshow(cm, interpolation='nearest', cmap=cmap)
#    plt.colorbar()
    tick_marks = np.arange(len(classes))
    plt.xticks(tick_marks, classes, rotation=0)
    plt.yticks(tick_marks, classes, rotation=0)
    fmt = '.2f' if normalize else 'd'
    thresh = cm.max() / 2.
    for i, j in itertools.product(range(cm.shape[0]), range(cm.shape[1])):
        plt.text(j, i, format(cm[i, j], fmt), fontsize=12,
                 horizontalalignment="center",
                 color="white" if cm[i, j] > thresh else "black")    
    plt.title(check+' confusion matrix')    
    plt.xlabel('Predicted label'); plt.ylabel('True label')    
    fig = plt.gcf(); plt.tight_layout(); plt.show()
    fig.savefig(os.path.join(save_dir, check+'_conf_matrix.png'), dpi=100)
    

# ROC curves   
def rocCurve(true_labels, predictions, classes, check):       
    n_classes = len(classes)
    true_labels = label_binarize(true_labels, np.linspace(0,n_classes,n_classes+1))
    true_labels = true_labels[:,:-1]
    fpr = dict()
    tpr = dict()
    roc_auc = dict()
    for i in range(len(classes)):
        fpr[i], tpr[i], _ = roc_curve(true_labels[:, i], predictions[:, i])
        roc_auc[i] = auc(fpr[i], tpr[i]) 
    # Compute micro-average ROC curve and ROC area
    fpr["micro"], tpr["micro"], _ = roc_curve(true_labels.ravel(), predictions.ravel())
    roc_auc["micro"] = auc(fpr["micro"], tpr["micro"])      
    all_fpr = np.unique(np.concatenate([fpr[i] for i in range(n_classes)]))   
    # Then interpolate all ROC curves at this points
    mean_tpr = np.zeros_like(all_fpr)
    for i in range(n_classes):
        mean_tpr += interp(all_fpr, fpr[i], tpr[i])  
    # Finally average it and compute AUC
    mean_tpr /= n_classes   
    fpr["macro"] = all_fpr
    tpr["macro"] = mean_tpr
    roc_auc["macro"] = auc(fpr["macro"], tpr["macro"])    
    # Plot all ROC curves
    plt.figure()
    if n_classes > 2:
        plt.plot(fpr["micro"], tpr["micro"], label='micro-avg. ROC curve (AUC = {0:0.2f})'
                 ''.format(roc_auc["micro"]), color='darkorange', linestyle=':', linewidth=3)   
        plt.plot(fpr["macro"], tpr["macro"], label='macro-avg. ROC curve (AUC = {0:0.2f})'           
                 ''.format(roc_auc["macro"]), color='indigo', linestyle=':', linewidth=3)            
    colors = cycle(['blue', 'green', 'red', 'cyan', 'magenta'])
    for i, color in zip(range(n_classes), colors):
        plt.plot(fpr[i], tpr[i], color=color, lw=1.5,
                 label='ROC curve of class {0} (AUC = {1:0.2f})' ''.format(i, roc_auc[i]))                
    plt.plot([0, 1], [0, 1], 'k--', lw=1)
    plt.xlim([0.0, 1.0])
    plt.ylim([0.0, 1.05])
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.title(check+' ROC curve')
    plt.legend(loc="lower right")
    plt.grid()
    fig = plt.gcf(); plt.tight_layout(); plt.show()
    fig.savefig(os.path.join(save_dir, check+'_ROC_curve.png'), dpi=100)


# Validatiion and test errors
def results(generator, check, save_flag=True):
    # Initialize variables 
    fnames = generator.filenames
    label2idx = generator.class_indices
    idx2label = dict((v,k) for k,v in label2idx.items())
    values = {}
    idx_list = []
    for key, value in idx2label.items():
        key = str(key)+':'
        value = str(value)
        temp = [key, value]
        idx_list += temp
    idx_list = ' '.join(map(str, idx_list))
        
    # Make predictions and find errors
    predictions = model.predict_generator(generator, steps=generator.samples/generator.batch_size, verbose=1)
    ground_truth = generator.classes
    predicted_classes = np.argmax(predictions, axis=1)
    pred_confidence = np.amax(predictions, axis=1)
    errors = np.where(predicted_classes != ground_truth)[0]
    print("{} errors = {}/{}".format(check,len(errors),len(generator.classes)))
    
    # List errors into the stats.csv
    statsWrite(' ')
    statsWrite(check+' errors: '+str(len(errors)))
    statsWrite('Actual-Pred-Confidence/F.name')
    statsWrite(idx_list)
    for i in range(len(errors)):          
        pred_result = [ground_truth[errors[i]], predicted_classes[errors[i]], 
                       pred_confidence[errors[i]], ',', fnames[errors[i]]]
        pred_result = ' '.join(map(str, pred_result))
        statsWrite(pred_result)
    
    # Write prediction output to the pickle file
    values['ground_truth'] = ground_truth
    values['predictions'] = predictions
    values['predicted_classes'] = predicted_classes
    values['label2idx'] = label2idx
    if save_flag: pickleSave(values)
    
    # Plot and return the outputs
    rocCurve(ground_truth, predictions, label2idx, check)
    confMatrix(ground_truth, predicted_classes, label2idx, check, normalize=False)
    return values


#%% Main function--------------------------------------------------------------
if __name__ == '__main__':
    # Image directories
    root_dir = os.getcwd()
    train_dir = os.path.join(root_dir, 'train')
    validation_dir = os.path.join(root_dir, 'validation')
    valrma_dir = os.path.join(root_dir, 'validation/rma')
    test_dir = os.path.join(root_dir, 'test')
    unident_dir = os.path.join(root_dir, 'unident')
    save_dir = os.path.join(root_dir, 'output')
    train_log_dir = os.path.join(save_dir, 'train_log.csv')
    trained_dir = os.path.join(root_dir, 'model.h5')
    if not os.path.exists(save_dir): os.mkdir(save_dir)
    places_weights = os.path.join(root_dir, 'places365.h5')
    if not os.path.exists(places_weights):
        places_notop_url = 'https://github.com/GKalliatakis/Keras-VGG16-places365/releases/download/v1.0/vgg16-places365_weights_tf_dim_ordering_tf_kernels_notop.h5'
        print('Places365 weights is downloading...')
        urllib.request.urlretrieve(places_notop_url, places_weights)
        print('Download completed')
      
    
    # Initialize stats file
    statsWrite('ORIENTATION CORRECTION CLASSIFIER')
    statsWrite('Start time: '+str(dt.datetime.now()))
    
    # Initialize the parameters
    train_batchsize = 256
    val_batchsize = 128
    tst_batchsize = 128
    epochs = 25
    lr = 0.5e-3 
       
    image_size = 224
    target_angles = [0, 90, 180, 270, 'undef']  
    tst_classes = dict(zip([str(i) for i in (target_angles)], list(range(len(target_angles)))))
    
    # Load the VGG model
    vgg_conv = VGG16(weights=places_weights, include_top=False, input_shape=(image_size, image_size, 3))
    
    # Freeze n number of layers from the last
    for layer in vgg_conv.layers[:11]: layer.trainable = False          
    
    # Create and compile the model
    model = createModel()
    LR_mult_dict = {}
    for layer in model.layers[:]: LR_mult_dict[layer.name] = 1
    LR_mult_dict['pred_layer'] = 10
    adam = Adam(lr=lr, decay=5e-5, multipliers=LR_mult_dict)
    model.compile(loss=[categorical_focal_loss(alpha=.25, gamma=2)], optimizer=adam, metrics=['acc'])
    
    # Create a callbacks list
    checkpoint = ModelCheckpoint(os.path.join(save_dir, 'best_model.h5'), monitor='val_acc', 
                                 verbose=1, save_best_only=True, mode='max')    
    early_stopping = EarlyStopping(patience=7)
    tensorboard = TensorBoard(log_dir=save_dir, batch_size=train_batchsize)
    logger = CSVLogger(train_log_dir, separator=',', append=False)    
    callbacks=[checkpoint, tensorboard, early_stopping, logger]

    # Data Generator for training data
    train_generator = RotNetGen(train_dir, target_size=(image_size,image_size), 
                                target_classes=target_angles, batch_size=train_batchsize,
                                preprocessing_function=preprocess_input, shuffle=True,
                                check_images=True, gauss_noise=20, brightness=50, contrast=1.5)
    
    # Data Generator for validation data
    validation_generator = RotNetGen(validation_dir, target_size=(image_size,image_size), 
                                     target_classes=target_angles, batch_size=val_batchsize, 
                                     preprocessing_function=preprocess_input, shuffle=True,
                                     check_images=True)
        
    # Train the Model   
    history = model.fit_generator(train_generator, 
                                  steps_per_epoch=train_generator.N/train_batchsize, 
                                  epochs=epochs, validation_data=validation_generator,                                   
                                  validation_steps=validation_generator.N/val_batchsize, 
                                  callbacks=callbacks, verbose=1)    
    
    # Save model and model history
    model.save(os.path.join(save_dir, 'model.h5'))
    pickleSave(history.history)
    
    # Write unreadibles and sample numbers to stats
    statsWrite(' ')   
    if train_generator.check_images:
        statsWrite('Train unreadible files: '+str(len(train_generator.unreadibles))+
                '/'+str(train_generator.N+len(train_generator.unreadibles)))
        for unreadible in train_generator.unreadibles:
            statsWrite(unreadible)
        
    if validation_generator.check_images:
        statsWrite('Validation unreadible files: '+str(len(validation_generator.unreadibles))+
                '/'+str(validation_generator.N+len(validation_generator.unreadibles)))
        for unreadible in validation_generator.unreadibles:
            statsWrite(unreadible)
            
    statsWrite('Train files: '+str(train_generator.N))
    statsWrite('Validation files: '+str(validation_generator.N))
    
    # Plot results and save numerical outputs to a pickle file
    # 0: history, 1: validation, 2: test
    val_outs = results(validation_generator, 'Validation')
    historyPlots(history)
    
    # Write stop time to stats
    statsWrite('Stop time: '+str(dt.datetime.now()))
    print('DONE SUCCESSFULLY')
    
    # Check returned images for testing purposes
#    images, classes = validation_generator.next()
#    for i in range(val_batchsize):
#        image = images[i]
#        plt.imshow(image)
#        plt.show()
