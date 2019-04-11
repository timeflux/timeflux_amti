Installation notes:

1. Connect the USB drive provided with the platform and follow the driver
   install instructions. Make sure you install the 64bit or 32bit version
   that matches your system.

2. Install the AMTINetForce software included on the USB drive.

   Run the ``USB for JN-22869 Calibration Data, Product Manuals & Software\Software & Drivers\NetForce Ver. 3.5.3\setup.exe``
   and **make sure** that you install it on the ``C:`` drive.

3. Run AMTINetForce.

   * The first time, it will automatically run the setup procedure.
   * Select Optima Gen USB 2
   * Find amplifiers / Configure the amplifier / Apply / Save / Done.
   * After this first time, the program will close and you will need to run it again.
   * Create an example subject, make sure you select "1" on the Amp ID below each graph, then click on start.
     You should see some signals on real time.

4. Copy / paste the 64-bit driver at some directory and **rename** it to ``AMTIUSBDevice.dll``


Note:

Do not use the DLL installed into ``C:\Windows\SysWOW64``, it does not work.
