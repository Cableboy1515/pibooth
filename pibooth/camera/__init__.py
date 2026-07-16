from pibooth.camera.base import BaseCamera
from pibooth.camera.gphoto import GpCamera, get_gp_camera_proxy
from pibooth.camera.hybrid import HybridCvCamera, HybridRpiCamera
from pibooth.camera.opencv import CvCamera, get_cv_camera_proxy
from pibooth.camera.rpi import RpiCamera, get_rpi_camera_proxy
from pibooth.utils import LOGGER

CAMERA_TYPES = ("auto", "picamera", "gphoto2", "opencv", "gphoto2+picamera", "gphoto2+opencv")


def close_proxy(rpi_cam_proxy, gp_cam_proxy, cv_cam_proxy):
    """Close proxy drivers."""
    if rpi_cam_proxy:
        RpiCamera(rpi_cam_proxy).quit()
    if gp_cam_proxy:
        GpCamera(gp_cam_proxy).quit()
    if cv_cam_proxy:
        CvCamera(cv_cam_proxy).quit()


def find_camera(camera_type: str = "auto") -> BaseCamera:
    """Initialize the camera depending of the connected one. In 'auto' mode
    the priority order is chosen in order to have best rendering during preview
    and to take captures. The gPhoto2 camera is first (drivers most restrictive)
    to avoid connection concurence in case of DSLR compatible with OpenCV.

    :param camera_type: force a camera backend, one of :py:data:`CAMERA_TYPES`
    """
    if camera_type not in CAMERA_TYPES:
        LOGGER.warning("Unknown camera type '%s' in config, fallback to 'auto'", camera_type)
        camera_type = "auto"

    need_rpi = camera_type in ("auto", "picamera", "gphoto2+picamera")
    need_gp = camera_type in ("auto", "gphoto2", "gphoto2+picamera", "gphoto2+opencv")
    need_cv = camera_type in ("auto", "opencv", "gphoto2+opencv")

    rpi_cam_proxy = get_rpi_camera_proxy() if need_rpi else None
    gp_cam_proxy = get_gp_camera_proxy() if need_gp else None
    cv_cam_proxy = get_cv_camera_proxy() if need_cv else None

    if camera_type == "auto":
        if rpi_cam_proxy and gp_cam_proxy:
            LOGGER.info("Configuring hybrid camera (Picamera + gPhoto2) ...")
            close_proxy(None, None, cv_cam_proxy)
            return HybridRpiCamera(rpi_cam_proxy, gp_cam_proxy)
        elif cv_cam_proxy and gp_cam_proxy:
            LOGGER.info("Configuring hybrid camera (OpenCV + gPhoto2) ...")
            close_proxy(rpi_cam_proxy, None, None)
            return HybridCvCamera(cv_cam_proxy, gp_cam_proxy)
        elif gp_cam_proxy:
            LOGGER.info("Configuring gPhoto2 camera ...")
            close_proxy(rpi_cam_proxy, None, cv_cam_proxy)
            return GpCamera(gp_cam_proxy)
        elif rpi_cam_proxy:
            LOGGER.info("Configuring Picamera camera ...")
            close_proxy(None, gp_cam_proxy, cv_cam_proxy)
            return RpiCamera(rpi_cam_proxy)
        elif cv_cam_proxy:
            LOGGER.info("Configuring OpenCV camera ...")
            close_proxy(rpi_cam_proxy, gp_cam_proxy, None)
            return CvCamera(cv_cam_proxy)
        raise OSError("Neither Raspberry Pi nor GPhoto2 nor OpenCV camera detected")

    if camera_type == "gphoto2+picamera" and rpi_cam_proxy and gp_cam_proxy:
        LOGGER.info("Configuring hybrid camera (Picamera + gPhoto2) ...")
        return HybridRpiCamera(rpi_cam_proxy, gp_cam_proxy)
    elif camera_type == "gphoto2+opencv" and cv_cam_proxy and gp_cam_proxy:
        LOGGER.info("Configuring hybrid camera (OpenCV + gPhoto2) ...")
        return HybridCvCamera(cv_cam_proxy, gp_cam_proxy)
    elif camera_type == "gphoto2" and gp_cam_proxy:
        LOGGER.info("Configuring gPhoto2 camera ...")
        return GpCamera(gp_cam_proxy)
    elif camera_type == "picamera" and rpi_cam_proxy:
        LOGGER.info("Configuring Picamera camera ...")
        return RpiCamera(rpi_cam_proxy)
    elif camera_type == "opencv" and cv_cam_proxy:
        LOGGER.info("Configuring OpenCV camera ...")
        return CvCamera(cv_cam_proxy)

    close_proxy(rpi_cam_proxy, gp_cam_proxy, cv_cam_proxy)
    raise OSError(f"Camera of type '{camera_type}' requested in config but not detected")
