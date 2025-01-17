import os
import time
import threading
from typing import List 
import tensorflow as tf
from tensorflow import keras
import numpy as np

from base.node import TEACHINGNode
from base.communication.packet import DataPacket
from .base_module import LearningModule


class StressModule(LearningModule):

    FED_TOPIC = 'stressmodule'

    def __init__(self):
        super(StressModule, self).__init__()
        self._build()
        self._calibration_steps = int(os.getenv('CALIBRATION_STEPS'))
        self._calibrate = bool(self._calibration_steps > 0)
        self._calibrated_count = 0
        self._sum = np.zeros((1,1))
        self._calibration_average = np.ones((1,1))
    
    @TEACHINGNode(produce=True, consume=True)
    def __call__(self, input_fn):
        for msg in input_fn:
            is_list = isinstance(msg.body, List)
            body = msg.body if is_list else [msg.body]
            stress = [] if is_list else None
            for body_t in body:
                if self._calibrate:
                    self._calibrated_count += 1
                    self._sum += np.array([[body_t['eda']]])
                    if self._calibrated_count == self._calibration_steps:
                        self._calibrate = False
                        self._calibration_average = self._sum / self._calibrated_count
                    print(f"Calibration step: {self._calibrated_count}/{self._calibration_steps}", flush=True)
                    continue
                else:
                    x = tf.constant([[[body_t['eda']]]])
                    x = x / self._calibration_average
                    if is_list:
                        stress.append({'stress': float(tf.squeeze(self._model(x)).numpy())})
                    else:
                        stress = {'stress': float(tf.squeeze(self._model(x)).numpy())}
            
                yield DataPacket(
                    topic='prediction.stress.value', 
                    timestamp=msg.timestamp,
                    body=stress)


    def _build(self):
        super(StressModule, self)._build()
        if self._model_path is not None and os.path.exists(self._model_path):
            self._model = tf.keras.models.load_model(self._model_path)
        else:
            inputs = tf.keras.Input(batch_shape=(1, 1, int(os.environ['INPUT_SIZE'])))
            for i in range(int(os.environ['LAYERS'])):
                x = ESN(
                    units=int(os.environ['UNITS']),
                    leaky=float(os.environ['LEAKY']),
                    spectral_radius=float(os.environ['RHO']),
                    connectivity_input=float(os.environ['CONNECTIVITY']),
                    return_sequences=True,
                    stateful=True
                )(inputs if i == 0 else x)
            outputs = tf.keras.layers.Dense(
                int(os.environ['N_CLASSES']), 
                activation=('sigmoid' if int(os.environ['N_CLASSES']) <= 2 else 'softmax')
            )(x)
            self._model = tf.keras.Model(inputs=inputs, outputs=outputs, name="stress_model")

        self._model.summary()


class ESN(keras.layers.RNN):
    """Echo State Network layer.
    This implements the recurrent layer using the ReservoirCell.
    Args:
        units: Positive integer, dimensionality of the reservoir.
        input_scaling: Float between 0 and 1.
            Desired scaling for the input.
            Default: 0.9.
        spectral_radius: Float between 0 and 1.
            Desired spectral radius of recurrent weight matrix.
            Default: 0.9.
        leaky: Float between 0 and 1.
            Desired leaking rate.
            Default: 1.
        connectivity_input: int.
            Number of connections between an input unit and a reservoir unit.
            Default: 10
        connectivity_recurrent: int.
            Connection probability between two reservoir units.
            Default: 10.
        
        use_bias: Boolean, whether the layer uses a bias vector.
            Default: True.
    Call arguments:
        inputs: A 3D tensor.
        mask: Binary tensor of shape `(samples, timesteps)` indicating whether
            a given timestep should be masked.
        training: Python boolean indicating whether the layer should behave in
            training mode or in inference mode. This argument is passed to the cell
            when calling it. This is only relevant if `dropout` or
            `recurrent_dropout` is used.
        initial_state: List of initial state tensors to be passed to the first
            call of the cell.
    """

    def __init__(
        self,
        units: int,
        input_scaling: float = 1.,
        spectral_radius: float = 0.9,
        leaky: float = 1.,
        connectivity_input: int = 10,
        connectivity_recurrent: int = 10,
        use_bias: bool = True,
        **kwargs,
    ):
        cell = ReservoirCell(
            units,
            input_scaling=input_scaling,
            spectral_radius=spectral_radius,
            leaky=leaky,
            connectivity_input=connectivity_input,
            connectivity_recurrent=connectivity_recurrent,
            use_bias=use_bias
        )
        super().__init__(
            cell,
            **kwargs,
        )

    def call(self, inputs, mask=None, training=None, initial_state=None):
        return super().call(
            inputs,
            mask=mask,
            training=training,
            initial_state=initial_state,
            constants=None,
        )

    @property
    def units(self):
        return self.cell.units

    @property
    def input_scaling(self):
        return self.cell.input_scaling

    @property
    def spectral_radius(self):
        return self.cell.spectral_radius

    @property
    def leaky(self):
        return self.cell.leaky
    
    @property
    def connectivity_input(self):
        return self.cell.connectivity_input
    
    @property
    def connectivity_recurrent(self):
        return self.cell.connectivity_recurrent

    @property
    def use_bias(self):
        return self.cell.use_bias

    def get_config(self):
        config = {
            "units": self.units,
            "input_scaling": self.input_scaling,
            "spectral_radius": self.spectral_radius,
            "leaky": self.leaky,
            "connectivity_input": self.connectivity_input,
            "connectivity_recurrent": self.connectivity_recurrent,
            "use_bias": self.use_bias
        }
        base_config = super().get_config()
        del base_config["cell"]
        return {**base_config, **config}

    @classmethod
    def from_config(cls, config):
        return cls(**config)


class ReservoirCell(keras.layers.AbstractRNNCell):
    """
    Implementation of a shallow reservoir to be used as cell of a Recurrent Neural Network
    
    Args:
    units: the number of recurrent neurons in the reservoir
    input_scaling: the max abs value of a weight in the input-reservoir connections
                    note that whis value also scales the unitary input bias 
    spectral_radius: the max abs eigenvalue of the recurrent weight matrix
    leaky: the leaking rate constant of the reservoir
    connectivity_input: number of outgoing connections from each input unit to the reservoir
    connectivity_recurrent: number of incoming recurrent connections for each reservoir unit
    """
    
    def __init__(self,
                 units: int,
                 input_scaling: float = 1.,
                 spectral_radius: float = 0.99,
                 leaky: float = 1., 
                 connectivity_input: int = 10, 
                 connectivity_recurrent: int = 10,
                 use_bias: bool = True,
                 **kwargs):
        
        self.units = units
        self.input_scaling = input_scaling
        self.spectral_radius = spectral_radius
        self.leaky = leaky
        self.connectivity_input = connectivity_input
        self.connectivity_recurrent = connectivity_recurrent
        self.use_bias = use_bias
        super().__init__(**kwargs)
        
    def build(self, input_shape):
        
        self.W_in = self.add_weight(
            "W_in", 
            shape=(input_shape[-1], self.units),
            initializer=sparse_tensor(self.connectivity_input, self.input_scaling),
            trainable=False
        )

        self.W_hat = self.add_weight(
            "W_hat",
            shape=(self.units, self.units),
            initializer=sparse_recurrent_tensor(self.spectral_radius, self.leaky, self.connectivity_recurrent),
            trainable=False
        )
        self.b = self.add_weight(
            "b",
            shape=(self.units,),
            initializer=keras.initializers.RandomUniform(minval=-self.input_scaling, maxval=self.input_scaling),
            trainable=False
        )
        
        self.alpha = self.add_weight(
            "leaky",
            shape=(),
            initializer=keras.initializers.Constant(self.leaky),
            trainable=False
        )

        self.built = True

    def call(self, inputs, states):
        prev_output = states[0]

        in_signal = inputs @ self.W_in + prev_output @ self.W_hat
        if self.use_bias:
            in_signal = in_signal + self.b
        output = (1-self.alpha)*prev_output + self.alpha * tf.nn.tanh(in_signal)
        
        return output, [output]
    
    def get_initial_state(self, inputs=None, batch_size=None, dtype=None):
        return tf.zeros((batch_size, self.state_size))
    
    @property
    def state_size(self):
        return self.units

    @property
    def output_size(self):
        return self.units


def sparse_eye(M):
    dense_shape = (M,M)
    indices = np.zeros((M,2))
    for i in range(M):
        indices[i,:] = [i,i]
    values = np.ones(shape = (M,)).astype('f')

    W = tf.sparse.reorder(tf.SparseTensor(indices=indices, values=values, dense_shape=dense_shape))
    return tf.sparse.to_dense(W)


def sparse_tensor(connectivity: int = 1, input_scaling: float = 1.):
    C = int(connectivity)

    def _initializer(shape, dtype=None, **kwargs):
        dense_shape = shape #the shape of the dense version of the matrix
        indices = np.zeros((shape[0] * C,2)) #indices of non-zero elements initialization
        k = 0
        for i in range(shape[0]):
            #the indices of non-zero elements in the i-th row of the matrix
            idx =np.random.choice(shape[1], size=C, replace=False)
            for j in range(C):
                indices[k,:] = [i,idx[j]] if shape[0] != shape[1] else [idx[j],i]
                k = k + 1
        values = 2*(2*np.random.rand(shape[0]*C).astype('f')-1)
        values *= input_scaling
        W = tf.sparse.reorder(tf.SparseTensor(indices=indices, values=values, dense_shape=dense_shape))
        return tf.sparse.to_dense(W)
    return _initializer


def sparse_recurrent_tensor(spectral_radius: float = 0.9, leaky: float = 1., connectivity: int = 1):

    def _initializer(shape, dtype=None, **kwargs):
        W = sparse_tensor(connectivity=connectivity)(shape)

        if leaky == 1:
            e,_ = tf.linalg.eig(W)
            rho = max(abs(e))
            W = W * (spectral_radius / rho)
            W_hat = W
        else:
            I = sparse_eye(shape[1])
            W2 = I * (1-leaky) + W * leaky
            e,_ = tf.linalg.eig(W2)
            rho = max(abs(e))
            W2 = W2 * (spectral_radius / rho)
            W_hat =  (W2 + I * (leaky - 1)) * (1/leaky)
        return W_hat

    return _initializer