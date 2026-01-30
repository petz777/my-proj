<?php
header("Content-Type: application/json");
header("Access-Control-Allow-Origin: *");

// === 1) TELEGRAM ОТЗЫВЫ ===
if (isset($_GET['send_review'])) {

    $input = json_decode(file_get_contents("php://input"), true);
    $message = $input["msg"] ?? "";

    // Твой токен + чат ID
    $token = "8472595146:AAHGLAuQ9r1Kbzg-jXYjZ9NXV3qDahQbWRI";
    $chat_id = "1777987653";

    $url = "https://api.telegram.org/bot$token/sendMessage";

    file_get_contents($url . "?chat_id=" . $chat_id . "&text=" . urlencode($message));

    echo json_encode(["status" => "ok"]);
    exit;
}


// ТВОЙ API-КЛЮЧ (partner token)
$partner_token = "t6zw4xbfj857djs9p5cb"; // <-- можешь заменить если нужно

// ID филиала
$branch_id = 792033;

// URL Yclients API: список услуг филиала
$url = "https://api.yclients.com/api/v1/services/$branch_id";

// Инициализация CURL:
$ch = curl_init($url);

curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    "Authorization: Bearer $partner_token",
    "Accept: application/vnd.yclients.v2+json",
]);

$response = curl_exec($ch);
$err = curl_error($ch);

curl_close($ch);

// Если ошибка — вернём её
if ($err) {
    echo json_encode(["error" => $err]);
    exit;
}

// Иначе — отдаём ответ от YClients клиенту
echo $response;
