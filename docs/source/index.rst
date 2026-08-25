pyobs-gemini
############

This is a `pyobs <https://www.pyobs.org>`_ (`documentation <https://docs.pyobs.org>`_) module for
`Optec Gemini <https://www.optecinc.com/astronomy/catalog/gemini/>`_ focuser/rotator units,
connected over a serial line.


Example configuration
**********************

This is an example configuration::

    class: pyobs_gemini.GeminiFocuserRotator

    # serial connection to the Gemini unit
    serial_config:
      port: /dev/ttyUSB0
      baudrate: 115200
      timeout: 0.1

    # constant offsets applied on top of the driver's own focus/rotation values
    focus_offset: 0.
    rotation_offset: 0.

    # communication
    comm:
      jid: test@example.com
      password: ***


Available classes
******************

There is one single class for Gemini units.

GeminiFocuserRotator
=====================
.. autoclass:: pyobs_gemini.GeminiFocuserRotator
   :members:
   :show-inheritance:
