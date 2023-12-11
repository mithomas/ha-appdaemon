from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import hassapi as hass
import math
import time


# inspired by https://github.com/mietzen/python-fritz-advanced-thermostat, specifically
# https://github.com/mietzen/python-fritz-advanced-thermostat/blob/main/fritz_advanced_thermostat/fritz_advanced_thermostat.py
class RecalibrateThermostatOffset(hass.Hass):

  def initialize(self):
    self.log("Init RecalibrateThermostatOffset")
    self.listen_event(self.recalibrate, "fritz_thermostat_recalibration_needed")

    self._host = "http://fritz.box"
    self._password = self.args["password"]

    self._timeout = 90


  def recalibrate(self, event, data, kvargs):
    thermostat_name = self.get_state(data["thermostat"], attribute="friendly_name")
    thermostat_temperature = float(self.get_state(data["thermostat"], attribute="current_temperature"))
    target_temperature = float(self.get_state(data["thermostat"], attribute="temperature"))
    room_temperature = float(self.get_state(data["thermometer"]))
    normalized_room_temperature = math.floor(room_temperature*2)/2 # err on the warmer side

    self.log(f"Recalibrate '{thermostat_name}' from {thermostat_temperature} to ~{normalized_room_temperature} ({room_temperature})")
    
    driver = self._get_selenium_driver()
    self._login(driver)
    self._navigate_to_device_details(driver, thermostat_name)

    recalibration_required = self._get_fritz_temperature(driver) != normalized_room_temperature

    if recalibration_required:
      self._recalibrate_device_offset(driver, thermostat_name, normalized_room_temperature)
      self._restore_target_temperature(driver, thermostat_name, target_temperature)
    else:
      self.log("No recalibration required")

    self._logout(driver)
    driver.quit()

  def _get_fritz_temperature(self, driver):
    while True:
      temperature = WebDriverWait(driver, self._timeout).until(EC.presence_of_element_located((By.ID, "uiNumDisplay:Roomtemp"))).text
      if temperature:
        return float(temperature.replace(',', '.'))
      time.sleep(0.5)

  def _get_target_temperature(self, driver, entity_row):
    while True:
      temperature = entity_row.find_element(By.XPATH, ".//span[@class='v-temperature__display']").text
      if temperature:
        return float(temperature.replace(',', '.').replace(' °C', ' '))
      time.sleep(0.5)


  def _get_selenium_driver(self):
    service = Service(executable_path='/usr/bin/chromedriver')

    selenium_options = Options()
    selenium_options.add_argument('--headless')
    selenium_options.add_argument('--no-sandbox')
    selenium_options.add_argument('--disable-gpu')
    selenium_options.add_argument('--disable-dev-shm-usage')
    selenium_options.add_argument("--window-size=1920,1200")

    return webdriver.Chrome(service=service, options=selenium_options)


  def _login(self, driver):
    driver.get(self._host)
    driver.find_element(By.ID, "uiPass").send_keys(self._password)
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "submitLoginBtn"))).click()

  def _navigate_to_device_details(self, driver, thermostat_name):
    # go to device overview
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "sh_menu"))).click()
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "sh_dev"))).click()
    # go to device
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.XPATH, f"//button[contains(@aria-label,'\"{thermostat_name}\" bearbeiten')]"))).click()

  def _recalibrate_device_offset(self, driver, thermostat_name, normalized_room_temperature):
    self.log(f"Currently set room temperature of {self._get_fritz_temperature(driver)} should be {normalized_room_temperature}")

    # adjust; get buttons inside loop as the UI only allows a maximum offset of +/- 5°C after which the buttons become disabled
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
    try:
      while self._get_fritz_temperature(driver) < normalized_room_temperature:
        WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "uiNumUp:Roomtemp"))).click()
      while self._get_fritz_temperature(driver) > normalized_room_temperature:
        WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "uiNumDown:Roomtemp"))).click()
    except TimeoutException:
      self.log(f"Adjust button disabled, maximum offset of +/- 5 is probably reached.")

    # confirm
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "uiMainApply"))).click()
    WebDriverWait(driver, self._timeout).until(EC.alert_is_present()).accept()
    self.log(f"Recalibrated '{thermostat_name}' to {self._get_fritz_temperature(driver)}")

  def _restore_target_temperature(self, driver, thermostat_name, target_temperature):
    # check for change to target temperature and possibly correct
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "sh_control"))).click()
    entity_row = WebDriverWait(driver, self._timeout).until(EC.presence_of_element_located((By.XPATH, f"//span[contains(text(),'{thermostat_name}')]/parent::div/parent::div")))

    if self._get_target_temperature(driver, entity_row) != target_temperature:
      self.log(f"Target temperature of {self._get_target_temperature(driver, entity_row)} should be {target_temperature}")
      buttons = entity_row.find_elements(By.XPATH, ".//button")
      button_down = buttons[1]
      button_up = buttons[2]
      while self._get_target_temperature(driver, entity_row) < target_temperature:
        button_up.click()
        time.sleep(1)
      while self._get_target_temperature(driver, entity_row) > target_temperature:
        button_down.click()
        time.sleep(1)
      self.log(f"Target temperature restored to {target_temperature}")
    else:
      self.log(f"Target temperature of {target_temperature} still valid, no restore required")

  def _logout(self, driver):
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "blueBarUserMenuIcon"))).click()
    WebDriverWait(driver, self._timeout).until(EC.element_to_be_clickable((By.ID, "logout"))).click()
