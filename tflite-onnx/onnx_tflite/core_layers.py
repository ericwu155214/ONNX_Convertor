"""Converters for core layers in Keras
"""
import onnx 
from onnx import helper
from onnx import AttributeProto, TensorProto

import numpy as np

#from . import helper
from base_layer import Layer
#from .exceptions import FeatureNotImplemented, OnnxNotSupport
import utils


class Dense(Layer):

  def __init__(self, previous_onnx_node_names, op_type, op_info, tflite_interpreter):
      Layer.__init__(self, previous_onnx_node_names, op_type, op_info, tflite_interpreter)

  def generate(self):
      fc_name = self.onnx_node_name

      weights_node_info = self.tflite_interpreter._get_tensor_details(self.op_info['inputs'][1])
      bias_node_info = self.tflite_interpreter._get_tensor_details(self.op_info['inputs'][2])

      weights_array = self.tflite_interpreter.get_tensor(weights_node_info['index'])
      bias_array = self.tflite_interpreter.get_tensor(bias_node_info['index'])

      # transpose because shape define diffent between tflite and onnx
      weights_array = np.transpose(weights_array, (1,0))


      # make weight onnx node
      weight_onnx_node_name = fc_name + "_weight"
      weight_onnx_node = onnx.helper.make_tensor(
          weight_onnx_node_name,
          TensorProto.FLOAT,
          weights_array.shape,
          weights_array.flatten().tolist()
      )

      # make bias onnx node
      bias_onnx_node_name = fc_name + "_bias"
      bias_onnx_node = onnx.helper.make_tensor(
          bias_onnx_node_name,
          TensorProto.FLOAT,
          bias_array.shape,
          bias_array.flatten().tolist()
      )

      # make FC onnx node
      self.previous_onnx_node_names.extend([weight_onnx_node_name, bias_onnx_node_name])
      fc_onnx_node = helper.make_node(
          op_type   = 'Gemm',
          inputs    = self.previous_onnx_node_names,
          outputs   = [fc_name],
          name      = fc_name,
          alpha     = 1.0,
          beta      = 1.0,
          transA    = 0,
          transB    = 0
      )

      out_shape_info = helper.make_tensor_value_info(
          fc_name,
          TensorProto.FLOAT,
          self.node_output_detail['shape'].tolist()
      )

      # update tables
      self.value_infos.append(out_shape_info)
      self.weight_node_list.append(weight_onnx_node)
      self.weight_node_list.append(bias_onnx_node)
      self.node_list.append(fc_onnx_node)

      if 'fused_activation_function' in self.op_info['builtin_options']:

          activative_op = self.op_info['builtin_options']['fused_activation_function']
          if activative_op == 'RELU6':
              clip_name = 'clip_' + self.onnx_node_name
              clip_node = onnx.helper.make_node(
                  'Clip',
                  inputs=[fc_name],
                  outputs=[clip_name],
                  min=0.0,
                  max=6.0,
                  name=clip_name
              )
              out_shape_info = helper.make_tensor_value_info(
                  clip_name,
                  TensorProto.FLOAT,
                  self.node_output_detail['shape'].tolist()
              )

              # update tables
              self.value_infos.append(out_shape_info)
              self.node_list.append(clip_node)

          elif activative_op == 'RELU':
              relu_name = 'relu_' + self.onnx_node_name
              relu_node = helper.make_node(
                  "Relu",
                  name=relu_name, 
                  inputs=[fc_name], 
                  outputs=[relu_name]
              )
              out_shape_info = helper.make_tensor_value_info(
                  relu_name,
                  TensorProto.FLOAT,
                  self.node_output_detail['shape'].tolist()
              )

              # update tables
              self.value_infos.append(out_shape_info)
              self.node_list.append(relu_node)

      return self.node_list, self.value_infos, self.weight_node_list

class Reshape(Layer):

  def __init__(self, previous_onnx_node_names, op_type, op_info, tflite_interpreter):
      Layer.__init__(self, previous_onnx_node_names, op_type, op_info, tflite_interpreter)

  def generate(self):
      out_dim = self.node_output_detail['shape']
      in_dim = self.node_input_detail['shape']

      dims = list(range(len(in_dim)))
      dims = dims[:1] + dims[2:] + dims[1:2]

      # add transpose
      transpose_before_node_name = 'transpose_node_before_reshape_' + self.onnx_node_name
      transpose_before_node = onnx.helper.make_node(
          'Transpose',
          inputs=self.previous_onnx_node_names,
          outputs=[transpose_before_node_name],
          perm=dims,
          name=transpose_before_node_name
      )
      # update tables
      self.node_list.append(transpose_before_node)


      reshape_node_name = self.onnx_node_name
      shape_tensor_name = 'shape_tensor_' + self.onnx_node_name
      shape_node_name = 'shape_const_' + self.onnx_node_name

      new_shape = np.array(self.op_info['builtin_options']['new_shape'], dtype='int64')
      shape_tensor = onnx.helper.make_tensor(shape_tensor_name,TensorProto.INT64,new_shape.shape, new_shape)
      shape_node = helper.make_node("Constant",[],[shape_node_name],name=shape_node_name,value=shape_tensor)

      reshape_node = onnx.helper.make_node(
          'Reshape',
          inputs=[transpose_before_node_name, shape_node_name],
          outputs=[reshape_node_name],
          name=reshape_node_name
      )

      # update tables
      self.node_list.append(shape_node)
      self.node_list.append(reshape_node)

      dims = list(range(len(out_dim)))
      dims = dims[:1] + dims[-1:] + dims[1:-1]
      # add transpose
      transpose_after_node_name = 'transpose_node_after_reshape_' + self.onnx_node_name
      transpose_after_node = onnx.helper.make_node(
          'Transpose',
          inputs=[reshape_node_name],
          outputs=[transpose_after_node_name],
          perm=dims,
          name=transpose_after_node_name
      )

      # update tables
      self.node_list.append(transpose_after_node)

      return self.node_list, self.value_infos, self.weight_node_list


class Pad(Layer):

  def __init__(self, previous_onnx_node_names, op_type, op_info, tflite_interpreter):
      Layer.__init__(self, previous_onnx_node_names, op_type, op_info, tflite_interpreter)

  def generate(self):
      # tflite pad :[[0 0]
      #             [0 1]           onnx pad :
      #             [0 1]     ==       [0,0,0,0,0,0,1,1]         
      #             [0 0]]          

      # create constant node
      pad_node_detail = self.tflite_interpreter._get_tensor_details(self.op_info['inputs'][1])
      pad_param = self.tflite_interpreter.get_tensor(pad_node_detail['index']).tolist()

      pad_w = pad_param[1][1]
      pad_h = pad_param[2][1]

          # build node
      pad_name = self.onnx_node_name
      pad_node = helper.make_node(
          'Pad', # op_type
          self.previous_onnx_node_names, # 輸入
          [pad_name], # 輸出
          mode='constant', 
          value=0.0, #名為 value 的屬性，資料型別（AttributeType）為 FLOAT
          pads=[0,0,0,0,0,0,pad_w,pad_h],#pad_param.flatten().tolist() #名為 pads 的屬性，資料型別（AttributeType）為 INTS 
          name=pad_name 
      )

      # update tables
      self.node_list.append(pad_node)

      return self.node_list, self.value_infos, self.weight_node_list