# -*- coding: utf-8 -*-
"""
/***************************************************************************
 DrypatchDockWidget
                                 A QGIS plugin
 This plugin detects dry patches from a satelite imagery layer 
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                             -------------------
        begin                : 2024-02-04
        git sha              : $Format:%H$
        copyright            : (C) 2024 by Tuhin Roychowdhury
        email                : rc.tuhin02@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os

import logging
import os
from typing import Optional

from qgis.core import Qgis, QgsMapLayerProxyModel, QgsProject
from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QComboBox, QFileDialog, QMessageBox

from dp_detector.common.config_entry_key import ConfigEntryKey
from dp_detector.common.defines import IS_DEBUG, PLUGIN_NAME
from dp_detector.common.errors import OperationFailedException
from dp_detector.common.processing_overlap import ProcessingOverlap, ProcessingOverlapOptions
from dp_detector.common.processing_parameters.detection_parameters import DetectionParameters, DetectorType
from dp_detector.common.processing_parameters.map_processing_parameters import (MapProcessingParameters, ModelOutputFormat,
                                                                             ProcessedAreaType)
#from dp_detector.common.processing_parameters.regression_parameters import RegressionParameters
#from dp_detector.common.processing_parameters.segmentation_parameters import SegmentationParameters
#from dp_detector.common.processing_parameters.superresolution_parameters import SuperresolutionParameters
from dp_detector.common.processing_parameters.training_data_export_parameters import TrainingDataExportParameters
from dp_detector.processing.models.detector import Detector
from dp_detector.processing.models.model_base import ModelBase
from dp_detector.processing.models.model_types import ModelDefinition, ModelType
from dp_detector.widgets.input_channels_mapping.input_channels_mapping_widget import InputChannelsMappingWidget
from dp_detector.widgets.training_data_export_widget.training_data_export_widget import TrainingDataExportWidget


FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'dp_detector_dockwidget_base.ui'))

class DryPatchDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    closingPlugin = pyqtSignal()
    run_model_inference_signal = pyqtSignal(MapProcessingParameters)  
    run_training_data_export_signal = pyqtSignal(TrainingDataExportParameters)

    def __init__(self, iface, parent=None):
        super(DryPatchDockWidget, self).__init__(parent)
        self.iface = iface
        self._model = None  # type: Optional[ModelBase]
        self.setupUi(self)

        self._input_channels_mapping_widget = InputChannelsMappingWidget(self)  
        self._training_data_export_widget = TrainingDataExportWidget(self)  # widget with UI for data export tool

        self._create_connections()
        self._setup_misc_ui()
        self._load_ui_from_config()


    def _load_ui_from_config(self):
        """ Load the UI values from the project configuration
        """
        layers = QgsProject.instance().mapLayers()

        try:
            input_layer_id = ConfigEntryKey.INPUT_LAYER_ID.get()
            if input_layer_id and input_layer_id in layers:
                self.mMapLayerComboBox_inputLayer.setLayer(layers[input_layer_id])

            processed_area_type_txt = ConfigEntryKey.PROCESSED_AREA_TYPE.get()
            self.comboBox_processedAreaSelection.setCurrentText(processed_area_type_txt)

            model_type_txt = ConfigEntryKey.MODEL_TYPE.get()
            self.comboBox_modelType.setCurrentText(model_type_txt)

            model_output_format_txt = ConfigEntryKey.MODEL_OUTPUT_FORMAT.get()
            self.comboBox_modelOutputFormat.setCurrentText(model_output_format_txt)

            self._input_channels_mapping_widget.load_ui_from_config()
            self._training_data_export_widget.load_ui_from_config()

            # NOTE: load the model after setting the model_type above
            model_file_path = ConfigEntryKey.MODEL_FILE_PATH.get()
            if model_file_path:
                self.lineEdit_modelPath.setText(model_file_path)
                self._load_model_and_display_info(abort_if_no_file_path=True)  # to prepare other ui components

            # needs to be loaded after the model is set up
            self.comboBox_outputFormatClassNumber.setCurrentIndex(ConfigEntryKey.MODEL_OUTPUT_FORMAT_CLASS_NUMBER.get())

            self.doubleSpinBox_resolution_cm_px.setValue(ConfigEntryKey.PREPROCESSING_RESOLUTION.get())
            self.spinBox_processingTileOverlapPercentage.setValue(ConfigEntryKey.PREPROCESSING_TILES_OVERLAP.get())


            self.doubleSpinBox_confidence.setValue(ConfigEntryKey.DETECTION_CONFIDENCE.get())
            self.doubleSpinBox_iouScore.setValue(ConfigEntryKey.DETECTION_IOU.get())
            self.checkBox_removeOverlappingDetections.setChecked(ConfigEntryKey.DETECTION_REMOVE_OVERLAPPING.get())
            self.comboBox_detectorType.setCurrentText(ConfigEntryKey.DETECTOR_TYPE.get())
        except Exception:
            logging.exception("Failed to load the ui state from config!")

    def _save_ui_to_config(self):
        """ Save value from the UI forms to the project config
        """
        ConfigEntryKey.MODEL_FILE_PATH.set(self.lineEdit_modelPath.text())
        ConfigEntryKey.INPUT_LAYER_ID.set(self._get_input_layer_id())
        ConfigEntryKey.MODEL_TYPE.set(self.comboBox_modelType.currentText())
        ConfigEntryKey.PROCESSED_AREA_TYPE.set(self.comboBox_processedAreaSelection.currentText())

        model_output_format = self.comboBox_modelOutputFormat.currentText()
        ConfigEntryKey.MODEL_OUTPUT_FORMAT.set(model_output_format)
        ConfigEntryKey.MODEL_OUTPUT_FORMAT_CLASS_NUMBER.set(self.comboBox_outputFormatClassNumber.currentIndex())

        ConfigEntryKey.PREPROCESSING_RESOLUTION.set(self.doubleSpinBox_resolution_cm_px.value())
        ConfigEntryKey.PREPROCESSING_TILES_OVERLAP.set(self.spinBox_processingTileOverlapPercentage.value())

        ConfigEntryKey.DETECTION_CONFIDENCE.set(self.doubleSpinBox_confidence.value())
        ConfigEntryKey.DETECTION_IOU.set(self.doubleSpinBox_iouScore.value())
        ConfigEntryKey.DETECTION_REMOVE_OVERLAPPING.set(self.checkBox_removeOverlappingDetections.isChecked())
        ConfigEntryKey.DETECTOR_TYPE.set(self.comboBox_detectorType.currentText())

        self._input_channels_mapping_widget.save_ui_to_config()
        self._training_data_export_widget.save_ui_to_config()

    def _rlayer_updated(self):
        self._input_channels_mapping_widget.set_rlayer(self._get_input_layer())

    def _setup_misc_ui(self):
        """ Setup some misceleounous ui forms
        """
        
        combobox = self.comboBox_processedAreaSelection
        for name in ProcessedAreaType.get_all_names():
            combobox.addItem(name)

        self.verticalLayout_inputChannelsMapping.addWidget(self._input_channels_mapping_widget)
        self.verticalLayout_trainingDataExport.addWidget(self._training_data_export_widget)

        self.mMapLayerComboBox_inputLayer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.mMapLayerComboBox_areaMaskLayer.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self._set_processed_area_mask_options()
        self._set_processing_overlap_enabled()

        for model_definition in ModelDefinition.get_model_definitions():
            self.comboBox_modelType.addItem(model_definition.model_type.value)

        for detector_type in DetectorType.get_all_display_values():
            self.comboBox_detectorType.addItem(detector_type)
        self._detector_type_changed()

        for output_format_type in ModelOutputFormat.get_all_names():
            self.comboBox_modelOutputFormat.addItem(output_format_type)
        self._model_output_format_changed()

        self._rlayer_updated()  # to force refresh the dependant ui elements

    def _set_processed_area_mask_options(self):
        show_mask_combobox = (self.get_selected_processed_area_type() == ProcessedAreaType.FROM_POLYGONS)
        self.mMapLayerComboBox_areaMaskLayer.setVisible(show_mask_combobox)
        self.label_areaMaskLayer.setVisible(show_mask_combobox)

    def get_selected_processed_area_type(self) -> ProcessedAreaType:
        combobox = self.comboBox_processedAreaSelection  # type: QComboBox
        txt = combobox.currentText()
        return ProcessedAreaType(txt)

    def _create_connections(self):
        self.pushButton_runInference.clicked.connect(self._run_inference)
        self.pushButton_runTrainingDataExport.clicked.connect(self._run_training_data_export)
        self.pushButton_browseModelPath.clicked.connect(self._browse_model_path)
        self.comboBox_processedAreaSelection.currentIndexChanged.connect(self._set_processed_area_mask_options)
        self.comboBox_modelType.currentIndexChanged.connect(self._model_type_changed)
        self.comboBox_detectorType.currentIndexChanged.connect(self._detector_type_changed)
        self.pushButton_reloadModel.clicked.connect(self._load_model_and_display_info)
        self.pushButton_loadDefaultModelParameters.clicked.connect(self._load_default_model_parameters)
        self.mMapLayerComboBox_inputLayer.layerChanged.connect(self._rlayer_updated)
        self.comboBox_modelOutputFormat.currentIndexChanged.connect(self._model_output_format_changed)
        self.radioButton_processingTileOverlapPercentage.toggled.connect(self._set_processing_overlap_enabled)
        self.radioButton_processingTileOverlapPixels.toggled.connect(self._set_processing_overlap_enabled)

    def _model_type_changed(self):
        model_type = ModelType(self.comboBox_modelType.currentText())

        segmentation_enabled = False
        detection_enabled = False
        regression_enabled = False
        superresolution_enabled = False

        if model_type == ModelType.SEGMENTATION:
            segmentation_enabled = True
        elif model_type == ModelType.DETECTION:
            detection_enabled = True
        elif model_type == ModelType.REGRESSION:
            regression_enabled = True
        elif model_type == ModelType.SUPERRESOLUTION:
            superresolution_enabled = True
        else:
            raise Exception(f"Unsupported model type ({model_type})!")
        detection_enabled = False
        
        self.mGroupBox_detectionParameters.setEnabled(detection_enabled)
    

    def _detector_type_changed(self):
        detector_type = DetectorType(self.comboBox_detectorType.currentText())
        self.label_detectorTypeDescription.setText(detector_type.get_formatted_description())

    def _model_output_format_changed(self):
        txt = self.comboBox_modelOutputFormat.currentText()
        model_output_format = ModelOutputFormat(txt)
        class_number_selection_enabled = bool(model_output_format == ModelOutputFormat.ONLY_SINGLE_CLASS_AS_LAYER)
        self.comboBox_outputFormatClassNumber.setEnabled(class_number_selection_enabled)
        
    def _set_processing_overlap_enabled(self):
        overlap_percentage_enabled = self.radioButton_processingTileOverlapPercentage.isChecked()
        self.spinBox_processingTileOverlapPercentage.setEnabled(overlap_percentage_enabled)
        
        overlap_pixels_enabled = self.radioButton_processingTileOverlapPixels.isChecked()
        self.spinBox_processingTileOverlapPixels.setEnabled(overlap_pixels_enabled)

    def _set_probability_threshold_enabled(self):
        self.doubleSpinBox_probabilityThreshold.setEnabled(self.checkBox_pixelClassEnableThreshold.isChecked())

    def _browse_model_path(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            'Select Model ONNX file...',
            os.path.expanduser('~'),
            'All files (*.*);; ONNX files (*.onnx)')
        if file_path:
            self.lineEdit_modelPath.setText(file_path)
            self._load_model_and_display_info()

    def _load_default_model_parameters(self):
        """
        Load the default parameters from model metadata
        """
        value = self._model.get_metadata_resolution()
        if value is not None:
            self.doubleSpinBox_resolution_cm_px.setValue(value)

        value = self._model.get_metadata_tile_size()
        if value is not None:
            self.spinBox_tileSize_px.setValue(value)

        value = self._model.get_metadata_tiles_overlap()
        if value is not None:
            self.spinBox_processingTileOverlapPercentage.setValue(value)

        value = self._model.get_metadata_model_type()
        if value is not None:
            print(f'{value =}')
            self.comboBox_modelType.setCurrentText(value)

        value = self._model.get_detector_type()
        if value is not None:
            self.comboBox_detectorType.setCurrentText(value)

        value = self._model.get_metadata_detection_confidence()
        if value is not None:
            self.doubleSpinBox_confidence.setValue(value)

        value = self._model.get_metadata_detection_iou_threshold()
        if value is not None:
            self.doubleSpinBox_iouScore.setValue(value)

        value = self._model.get_metadata_detection_remove_overlapping()
        if value is not None:
            self.checkBox_removeOverlappingDetections.setChecked(value)

    def _load_model_with_type_from_metadata(self, model_class_from_ui, file_path):
        """
        If model has model_type in metadata - use this type to create proper model class.
        Otherwise model_class_from_ui will be used
        """
        model_class = model_class_from_ui

        model_type_str_from_metadata = ModelBase.get_model_type_from_metadata(file_path)
        if model_type_str_from_metadata is not None:
            model_type = ModelType(model_type_str_from_metadata)
            model_class = ModelDefinition.get_definition_for_type(model_type).model_class
            self.comboBox_modelType.setCurrentText(model_type.value)

        print(f'{model_type_str_from_metadata = }, {model_class = }')

        model = model_class(file_path)
        return model

    def _load_model_and_display_info(self, abort_if_no_file_path: bool = False):
        """
        Tries to load the model and display its message.
        """
        file_path = self.lineEdit_modelPath.text()

        if not file_path and abort_if_no_file_path:
            return

        txt = ''

        model_definition = self.get_selected_model_class_definition()
        model_class = model_definition.model_class
        self._model = self._load_model_with_type_from_metadata(
            model_class_from_ui=model_class,
            file_path=file_path)
        self._model.check_loaded_model_outputs()
        input_0_shape = self._model.get_input_shape()
        txt += f'Input shape: {input_0_shape}   =   [BATCH_SIZE * CHANNELS * SIZE * SIZE]'
        input_size_px = input_0_shape[-1]

        #Handling the input
        self.spinBox_tileSize_px.setValue(input_size_px)
        self.spinBox_tileSize_px.setEnabled(False)
        self._input_channels_mapping_widget.set_model(self._model)

        if isinstance(self._model, Detector):
            detector_type = DetectorType(self.comboBox_detectorType.currentText())
            self._model.set_model_type_param(detector_type)

        self._update_model_output_format_mapping()

    def _update_model_output_format_mapping(self):
        self.comboBox_outputFormatClassNumber: QComboBox
        self.comboBox_outputFormatClassNumber.clear()
        if not self._model:
            return

        for output_number in range(self._model.get_number_of_output_channels()):
            name = f'{output_number} - {self._model.get_channel_name(output_number)}'
            self.comboBox_outputFormatClassNumber.addItem(name)

    def get_mask_layer_id(self):
        if not self.get_selected_processed_area_type() == ProcessedAreaType.FROM_POLYGONS:
            return None

        mask_layer_id = self.mMapLayerComboBox_areaMaskLayer.currentLayer().id()
        return mask_layer_id

    def _get_input_layer(self):
        return self.mMapLayerComboBox_inputLayer.currentLayer()

    def _get_input_layer_id(self):
        layer = self._get_input_layer()
        if layer:
            return layer.id()
        else:
            return ''

    def _get_overlap_parameter(self):
        if self.radioButton_processingTileOverlapPercentage.isChecked():
            return ProcessingOverlap(
                selected_option=ProcessingOverlapOptions.OVERLAP_IN_PERCENT,
                percentage=self.spinBox_processingTileOverlapPercentage.value(),
            )
        elif self.radioButton_processingTileOverlapPixels.isChecked():
            return ProcessingOverlap(
                selected_option=ProcessingOverlapOptions.OVERLAP_IN_PIXELS,
                overlap_px=self.spinBox_processingTileOverlapPixels.value(),
            )
        else:
            raise Exception('Something goes wrong. No overlap parameter selected!')

    def _get_pixel_classification_threshold(self):
        if not self.checkBox_pixelClassEnableThreshold.isChecked():
            return 0
        return self.doubleSpinBox_probabilityThreshold.value()

    def get_selected_model_class_definition(self) -> ModelDefinition:
        """
        Get the currently selected model class (in UI)
        """
        model_type_txt = self.comboBox_modelType.currentText()
        model_type = ModelType(model_type_txt)
        model_definition = ModelDefinition.get_definition_for_type(model_type)
        return model_definition

    def get_inference_parameters(self) -> MapProcessingParameters:
        """ Get the parameters for the model interface.
        The returned type is derived from `MapProcessingParameters` class, depending on the selected model type.
        """
        map_processing_parameters = self._get_map_processing_parameters()

        if self._model is None:
            raise OperationFailedException("Please select and load a model first!")

        model_type = self.get_selected_model_class_definition().model_type
        if model_type == ModelType.DETECTION:
            params = self.get_detection_parameters(map_processing_parameters)
        return params

    def get_detection_parameters(self, map_processing_parameters: MapProcessingParameters) -> DetectionParameters:

        params = DetectionParameters(
            **map_processing_parameters.__dict__,
            confidence=self.doubleSpinBox_confidence.value(),
            iou_threshold=self.doubleSpinBox_iouScore.value(),
            remove_overlapping_detections=self.checkBox_removeOverlappingDetections.isChecked(),
            model=self._model,
            detector_type=DetectorType(self.comboBox_detectorType.currentText()),
        )

        return params

    def _get_map_processing_parameters(self) -> MapProcessingParameters:
        """
        Get common parameters for inference and exporting
        """
        processed_area_type = self.get_selected_processed_area_type()
        params = MapProcessingParameters(
            resolution_cm_per_px=self.doubleSpinBox_resolution_cm_px.value(),
            tile_size_px=self.spinBox_tileSize_px.value(),
            processed_area_type=processed_area_type,
            mask_layer_id=self.get_mask_layer_id(),
            input_layer_id=self._get_input_layer_id(),
            processing_overlap=self._get_overlap_parameter(),
            input_channels_mapping=self._input_channels_mapping_widget.get_channels_mapping(),
            model_output_format=ModelOutputFormat(self.comboBox_modelOutputFormat.currentText()),
            model_output_format__single_class_number=self.comboBox_outputFormatClassNumber.currentIndex(),
        )
        return params

    def _run_inference(self):
        # check_required_packages_and_install_if_necessary()
        try:
            params = self.get_inference_parameters()

            if not params.input_layer_id:
                raise OperationFailedException("Please select an input layer first!")
        except OperationFailedException as e:
            msg = str(e)
            self.iface.messageBar().pushMessage(PLUGIN_NAME, msg, level=Qgis.Warning, duration=7)
            logging.exception(msg)
            QMessageBox.critical(self, "Error!", msg)
            return

        self._save_ui_to_config()
        self.run_model_inference_signal.emit(params)

    def _run_training_data_export(self):
        # check_required_packages_and_install_if_necessary()
        try:
            map_processing_parameters = self._get_map_processing_parameters()
            training_data_export_parameters = self._training_data_export_widget.get_training_data_export_parameters(
                map_processing_parameters)

            if not map_processing_parameters.input_layer_id:
                raise OperationFailedException("Please select an input layer first!")

            training_data_export_parameters.input_channels_mapping = \
                self._input_channels_mapping_widget.get_channels_mapping_for_training_data_export()
        except OperationFailedException as e:
            msg = str(e)
            self.iface.messageBar().pushMessage(PLUGIN_NAME, msg, level=Qgis.Warning)
            logging.exception(msg)
            QMessageBox.critical(self, "Error!", msg)
            return

        self._save_ui_to_config()
        self.run_training_data_export_signal.emit(training_data_export_parameters)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()
