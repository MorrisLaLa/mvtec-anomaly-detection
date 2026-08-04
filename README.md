# MVTec 視覺瑕疵檢測

## 問題
工業現場正常樣本多、瑕疵樣本極少。用**無監督異常偵測**只學「正常長什麼樣」,
在測試時揪出並**定位**瑕疵(輸出熱力圖)。

## 資料
MVTec AD(15 品類,train 只有正常樣本,test 含瑕疵,ground_truth 提供像素級遮罩)。

## 方法
PatchCore / PaDiM(anomalib),只用 train/good 訓練。

## 如何執行
(待補)
