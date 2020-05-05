from pathlib import Path

import mmcv
import numpy as np
from mmedit.core.mask import (bbox2mask, brush_stroke_mask, get_irregular_mask,
                              random_bbox)
from mmedit.utils import FileClient

from ..registry import PIPELINES


@PIPELINES.register_module
class LoadImageFromFile(object):
    """Load image from file.

    Args:
        io_backend (str): io backend where images are store. Default: 'disk'.
        key (str): Keys in results to find corresponding path. Default: 'gt'.
        flag (str): Loading flag for images. Default: 'color'.
        save_original_img (bool): If True, maintain a copy of the image in
            `results` dict with name of `f'ori_{key}'`. Default: False.
        kwargs (dict): Args for file client.
    """

    def __init__(self,
                 io_backend='disk',
                 key='gt',
                 flag='color',
                 save_original_img=False,
                 **kwargs):
        self.io_backend = io_backend
        self.key = key
        self.flag = flag
        self.save_original_img = save_original_img
        self.kwargs = kwargs
        self.file_client = None

    def __call__(self, results):
        if self.file_client is None:
            self.file_client = FileClient(self.io_backend, **self.kwargs)
        filepath = str(results[f'{self.key}_path'])
        img_bytes = self.file_client.get(filepath)
        img = mmcv.imfrombytes(img_bytes, flag=self.flag)  # HWC, BGR
        if img.ndim == 2:
            img = np.expand_dims(img, axis=2)

        results[self.key] = img
        results[f'{self.key}_path'] = filepath
        results[f'{self.key}_ori_shape'] = img.shape
        if self.save_original_img:
            results[f'ori_{self.key}'] = img.copy()

        return results

    def __repr__(self):
        repr_str = self.__class__.__name__
        repr_str += (
            f'(io_backend={self.io_backend}, key={self.key}, '
            f'flag={self.flag}, save_original_img={self.save_original_img})')
        return repr_str


@PIPELINES.register_module
class LoadImageFromFileList(LoadImageFromFile):
    """Load image from file list.

    It accepts a list of path and read each frame from each path. A list
    of frames will be returned.

    Args:
        io_backend (str): io backend where images are store. Default: 'disk'.
        key (str): Keys in results to find corresponding path. Default: 'gt'.
        flag (str): Loading flag for images. Default: 'color'.
        save_original_img (bool): If True, maintain a copy of the image in
            `results` dict with name of `f'ori_{key}'`. Default: False.
        kwargs (dict): Args for file client.
    """

    def __call__(self, results):

        if self.file_client is None:
            self.file_client = FileClient(self.io_backend, **self.kwargs)
        filepaths = results[f'{self.key}_path']
        if not isinstance(filepaths, list):
            raise TypeError(
                f'filepath should be list, but got {type(filepaths)}')

        filepaths = [str(v) for v in filepaths]

        imgs = []
        shapes = []
        if self.save_original_img:
            ori_imgs = []
        for filepath in filepaths:
            img_bytes = self.file_client.get(filepath)
            img = mmcv.imfrombytes(img_bytes, flag=self.flag)  # HWC, BGR
            if img.ndim == 2:
                img = np.expand_dims(img, axis=2)
            imgs.append(img)
            shapes.append(img.shape)
            if self.save_original_img:
                ori_imgs.append(img.copy())

        results[self.key] = imgs
        results[f'{self.key}_path'] = filepaths
        results[f'{self.key}_ori_shape'] = shapes
        if self.save_original_img:
            results[f'ori_{self.key}'] = ori_imgs

        return results


@PIPELINES.register_module
class LoadAlpha(LoadImageFromFile):
    """Using OpenCV to read image.

    Required keys are "alpha_path", added or modified keys are "alpha",
    "ori_alpha", "ori_shape", "img_shape" and "img_name".
    """

    def __call__(self, results):
        if self.file_client is None:
            self.file_client = FileClient(self.io_backend, **self.kwargs)
        filepath = str(results[f'{self.key}_path'])
        img_bytes = self.file_client.get(filepath)
        alpha = mmcv.imfrombytes(img_bytes, flag=self.flag)  # HWC, BGR
        img_name = Path(results[f'{self.key}_path']).name
        assert alpha.shape[0], f"{img_name}'s alpha is not valid"
        results['alpha'] = alpha
        results['img_name'] = img_name
        results['ori_alpha'] = alpha
        results['ori_shape'] = alpha.shape
        results['img_shape'] = alpha.shape
        return results


@PIPELINES.register_module
class RandomLoadResizeBg(object):
    """Randomly load a background image and resize it.

    Required key is "img_shape", added key is "bg".

    Args:
        bg_dir (str): Path of directory to load background images from.
        io_backend (str): io backend where images are store. Default: 'disk'.
        flag (str): Loading flag for images. Default: 'color'.
        kwargs (dict): Args for file client.
    """

    def __init__(self, bg_dir, io_backend='disk', flag='color', **kwargs):
        self.bg_dir = bg_dir
        self.bg_list = list(mmcv.scandir(bg_dir))
        self.io_backend = io_backend
        self.flag = flag
        self.kwargs = kwargs
        self.file_client = None

    def __call__(self, results):
        if self.file_client is None:
            self.file_client = FileClient(self.io_backend, **self.kwargs)
        h, w = results['img_shape']
        idx = np.random.randint(len(self.bg_list))
        filepath = Path(self.bg_dir).joinpath(self.bg_list[idx])
        img_bytes = self.file_client.get(filepath)
        img = mmcv.imfrombytes(img_bytes, flag=self.flag)  # HWC, BGR
        bg = mmcv.imresize(img, (w, h), interpolation='bicubic')
        results['bg'] = bg
        return results

    def __repr__(self):
        return self.__class__.__name__ + f"(bg_dir='{self.bg_dir}')"


@PIPELINES.register_module
class LoadMask(object):
    """Load Mask for multiple types.

    For different types of mask, users need to provide the corresponding
    config dict.

    Example config for bbox:
        config = dict(img_shape=(256, 256), max_bbox_shape=128)

    Example config for irregular:
        config = dict(
            img_shape=(256, 256),
            num_vertexes=(4, 12),
            max_angle=4.,
            length_range=(10, 100),
            brush_width=(10, 40),
            area_ratio_range=(0.15, 0.5))

    Example config for ff:
        config = dict(
            img_shape=(256, 256),
            num_vertexes=(4, 12),
            mean_angle=1.2,
            angle_range=0.4,
            brush_width=(12, 40))

    Example config for set:
        config = dict(
            mask_list_file='xxx/xxx/ooxx.txt',
            prefix='/xxx/xxx/ooxx/',
            io_backend='disk',
            flag='unchanged',
            file_client_kwargs=dict()
        )
        The mask_list_file contains the list of mask file name like this:
            test1.jpeg
            test2.jpeg
            ...
            ...

        The prefix gives the data path.

    Attributes:
        mask_mode (str): Mask mode in ['bbox', 'irregular', 'ff', 'set'].
            bbox: square bounding box masks.
            irregular: irregular holes.
            ff: free-form holes from DeepFillv2.
            set: randomly get a mask from a mask set.
        mask_config (dict): Params for creating masks. Each type of mask needs
            different configs.
    """

    def __init__(self, mask_mode='bbox', mask_config=None):
        self.mask_mode = mask_mode
        self.mask_config = dict() if mask_config is None else mask_config
        assert isinstance(self.mask_config, dict)

        # set init info if needed in some modes
        self._init_info()

    def _init_info(self):
        if self.mask_mode == 'set':
            # get mask list information
            self.mask_list = []
            mask_list_file = self.mask_config['mask_list_file']
            with open(mask_list_file, 'r') as f:
                for line in f:
                    line_split = line.strip().split(' ')
                    mask_name = line_split[0]
                    self.mask_list.append(
                        Path(self.mask_config['prefix']).joinpath(mask_name))
            self.mask_set_size = len(self.mask_list)
            self.io_backend = self.mask_config['io_backend']
            self.flag = self.mask_config['flag']
            self.file_client_kwargs = self.mask_config['file_client_kwargs']
            self.file_client = None

    def _get_random_mask_from_set(self):
        if self.file_client is None:
            self.file_client = FileClient(self.io_backend,
                                          **self.file_client_kwargs)
        # minus 1 to avoid out of range error
        mask_idx = np.random.randint(0, self.mask_set_size)

        mask_bytes = self.file_client.get(self.mask_list[mask_idx])
        mask = mmcv.imfrombytes(mask_bytes, flag=self.flag)  # HWC, BGR
        if mask.ndim == 2:
            mask = np.expand_dims(mask, axis=2)
        else:
            mask = mask[:, :, 0:1]

        return mask

    def __call__(self, results):
        if self.mask_mode == 'bbox':
            mask_bbox = random_bbox(**self.mask_config)
            mask = bbox2mask(self.mask_config['img_shape'], mask_bbox)
            results['mask_bbox'] = mask_bbox
        elif self.mask_mode == 'irregular':
            mask = get_irregular_mask(**self.mask_config)
        elif self.mask_mode == 'set':
            mask = self._get_random_mask_from_set()
        elif self.mask_mode == 'ff':
            mask = brush_stroke_mask(**self.mask_config)
        else:
            raise NotImplementedError(
                f'Mask mode {self.mask_mode} has not been implemented.')
        results['mask'] = mask
        return results

    def __repr__(self):
        return self.__class__.__name__ + f"(mask_mode='{self.mask_mode}')"